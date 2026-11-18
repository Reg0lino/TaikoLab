"""Pure-mido MIDI Ingest with SyncToolbox Macro DTW + Isolated Demucs Drum Attack Snap."""

from __future__ import annotations

from pathlib import Path

import mido
import numpy as np
import soundfile as sf

from taikolab.ingest.stem_loader import discover_song_files, get_audio_duration, separate_stems_demucs
from taikolab.schemas import BeatEvent, DrumClass, DrumHit, OnsetEvent, SongAnalysis, TempoRegion

_GM_DRUM_MAP: dict[int, DrumClass] = {
    35: "kick",
    36: "kick",
    37: "snare",
    38: "snare",
    39: "snare",
    40: "snare",
    41: "tom",
    43: "tom",
    45: "tom",
    47: "tom",
    48: "tom",
    50: "tom",
    42: "hihat",
    44: "hihat",
    46: "ride",
    49: "crash",
    52: "crash",
    55: "crash",
    57: "crash",
    51: "ride",
    53: "ride",
    59: "ride",
}

_AUX_PERCUSSION_KEYWORDS = ("shaker", "tambourine", "claps", "clap", "cowbell", "triangle", "cabasa", "maraca")
_VOCAL_KEYWORDS = ("vocal", "voice", "vox", "sing", "melody", "lyric")
_GUITAR_KEYWORDS = ("guitar", "gtr", "rhythm", "lead", "distorted", "overdrive", "clean", "bass")


def _ensure_clean_simulator_audio(song_dir: Path, main_audio: Path) -> Path:
    """Decode VBR/corrupt-Xing MP3 once into clean PCM audio_clean.wav for exact sync."""
    if main_audio.suffix.lower() in (".wav", ".ogg"):
        return main_audio

    clean_wav = song_dir / "audio_clean.wav"
    if clean_wav.exists() and clean_wav.stat().st_size > 10000:
        return clean_wav

    import librosa

    print(f"[TaikoLab TabSync] Decoding {main_audio.name} -> audio_clean.wav (fixes MP3 seek drift)...")
    y, sr = librosa.load(str(main_audio), sr=44100, mono=False)
    if y.ndim == 2:
        y = y.T
    sf.write(str(clean_wav), y, sr, subtype="PCM_16")
    return clean_wav


def _build_tick_to_sec(mid: mido.MidiFile) -> tuple[callable, list[tuple[int, int, int]], int]:
    ppq = mid.ticks_per_beat
    meta_events: list[tuple[int, int, mido.Message]] = []
    max_tick = 0

    for tr in mid.tracks:
        tk = 0
        for msg in tr:
            tk += msg.time
            if msg.type == "set_tempo":
                meta_events.append((tk, 0, msg))
            elif msg.type == "time_signature":
                meta_events.append((tk, 1, msg))
        max_tick = max(max_tick, tk)

    meta_events.sort(key=lambda x: (x[0], x[1]))

    tempo_segments: list[tuple[int, float, int]] = [(0, 0.0, 500000)]
    ts_events: list[tuple[int, int, int]] = [(0, 4, 4)]

    cur_sec = 0.0
    prev_tk = 0
    cur_tempo = 500000

    for tk, kind, msg in meta_events:
        if kind == 0:
            if tk > prev_tk:
                cur_sec += mido.tick2second(tk - prev_tk, ppq, cur_tempo)
                prev_tk = tk
            cur_tempo = int(msg.tempo)
            if tempo_segments[-1][0] == tk:
                tempo_segments[-1] = (tk, cur_sec, cur_tempo)
            else:
                tempo_segments.append((tk, cur_sec, cur_tempo))
        else:
            ts_events.append((tk, int(msg.numerator), int(msg.denominator)))

    def tick_to_sec(target_tk: int) -> float:
        idx = 0
        for i, (tk, _, _) in enumerate(tempo_segments):
            if tk <= target_tk:
                idx = i
            else:
                break
        seg_tk, seg_sec, seg_tmp = tempo_segments[idx]
        return seg_sec + mido.tick2second(target_tk - seg_tk, ppq, seg_tmp)

    return tick_to_sec, ts_events, max_tick


def _parse_midi_unified(
    midi_path: Path,
) -> tuple[
    list[tuple[float, float, float, int, int]],
    list[DrumHit],
    list[OnsetEvent],
    list[OnsetEvent],
    list[tuple[float, float, int, bool]],
]:
    """Parse bars, drums, vocals, guitars, and sustained synth guide notes on ONE mido timeline."""
    mid = mido.MidiFile(str(midi_path))
    ppq = mid.ticks_per_beat
    tick_to_sec, ts_events, max_tick = _build_tick_to_sec(mid)

    def active_ts(target_tk: int) -> tuple[int, int]:
        num, den = 4, 4
        for tk, n, d in ts_events:
            if tk <= target_tk:
                num, den = n, d
            else:
                break
        return num, den

    bars: list[tuple[float, float, float, int, int]] = []
    cur_tk = 0
    while cur_tk < max_tick:
        num, den = active_ts(cur_tk)
        bar_ticks = max(1, int(round(ppq * 4.0 * num / den)))
        next_tk = cur_tk + bar_ticks
        t0 = tick_to_sec(cur_tk)
        t1 = tick_to_sec(next_tk)
        dur = max(0.2, t1 - t0)
        bpm = max(30.0, min(400.0, 240.0 * num / (den * dur)))
        bars.append((t0, t1, bpm, num, den))
        cur_tk = next_tk

    channel_programs: dict[int, int] = {}
    all_notes: list[tuple[float, float, int, int, int, str]] = []

    for tr in mid.tracks:
        t_name = (tr.name or "").lower()
        if any(k in t_name for k in _AUX_PERCUSSION_KEYWORDS):
            continue
        tk = 0
        open_notes: dict[tuple[int, int], tuple[float, int]] = {}
        for msg in tr:
            tk += msg.time
            if msg.type == "program_change":
                channel_programs[msg.channel] = int(msg.program)
            elif msg.type == "note_on" and msg.velocity > 0:
                t_sec = tick_to_sec(tk)
                key = (msg.channel, int(msg.note))
                if key in open_notes:
                    s_sec, s_vel = open_notes.pop(key)
                    all_notes.append((s_sec, max(s_sec + 0.04, t_sec), msg.channel, int(msg.note), s_vel, t_name))
                open_notes[key] = (t_sec, int(msg.velocity))
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                key = (msg.channel, int(msg.note))
                if key in open_notes:
                    s_sec, s_vel = open_notes.pop(key)
                    e_sec = tick_to_sec(tk)
                    all_notes.append((s_sec, max(s_sec + 0.04, e_sec), msg.channel, int(msg.note), s_vel, t_name))

        for (ch, pitch), (s_sec, s_vel) in open_notes.items():
            all_notes.append((s_sec, s_sec + 0.25, ch, pitch, s_vel, t_name))

    all_notes.sort(key=lambda x: x[0])

    drums: list[DrumHit] = []
    vocals: list[OnsetEvent] = []
    raw_guitars: list[OnsetEvent] = []
    synth_guide: list[tuple[float, float, int, bool]] = []

    seen_drum_cls: dict[DrumClass, float] = {}
    last_vocal_t = -1.0

    for s_sec, e_sec, ch, pitch, raw_vel, t_name in all_notes:
        vel = max(0.2, min(1.0, float(raw_vel) / 127.0))
        is_drum = (ch == 9)
        synth_guide.append((s_sec, min(e_sec, s_sec + 1.8), pitch, is_drum))

        if is_drum:
            d_cls = _GM_DRUM_MAP.get(pitch, "unknown")
            if d_cls in seen_drum_cls and (s_sec - seen_drum_cls[d_cls]) < 0.015:
                continue
            seen_drum_cls[d_cls] = s_sec
            drums.append(DrumHit(time_sec=round(s_sec, 4), drum_class=d_cls, velocity=round(vel, 3), source="tab"))
            continue

        prog = channel_programs.get(ch, 0)
        is_vocal_track = any(k in t_name for k in _VOCAL_KEYWORDS)
        is_guitar_track = any(k in t_name for k in _GUITAR_KEYWORDS) or (24 <= prog <= 39)

        if is_vocal_track or ((not is_guitar_track) and ((80 <= prog <= 87) or (52 <= prog <= 55))):
            if (s_sec - last_vocal_t) >= 0.045:
                last_vocal_t = s_sec
                hz = 440.0 * (2.0 ** ((pitch - 69) / 12.0))
                vocals.append(
                    OnsetEvent(
                        time_sec=round(s_sec, 4),
                        strength=round(vel, 3),
                        duration_sec=round(max(0.0, e_sec - s_sec), 3),
                        pitch_hz=round(hz, 1),
                    )
                )
        elif is_guitar_track:
            raw_guitars.append(OnsetEvent(time_sec=round(s_sec, 4), strength=round(vel, 3)))

    raw_guitars.sort(key=lambda e: e.time_sec)
    guitars: list[OnsetEvent] = []
    for g in raw_guitars:
        if guitars and abs(g.time_sec - guitars[-1].time_sec) <= 0.025:
            guitars[-1].strength = max(guitars[-1].strength, g.strength)
        else:
            guitars.append(g)

    return bars, drums, vocals, guitars, synth_guide


def _synthesize_midi_guide(
    synth_guide: list[tuple[float, float, int, bool]],
    duration_sec: float,
    fs: int = 22050,
) -> np.ndarray:
    """Render full-duration guitar/bass/vocal tones + crisp drum clicks for SyncToolbox DTW."""
    max_t = max([duration_sec] + [n[1] for n in synth_guide]) + 2.0
    y = np.zeros(int(max_t * fs), dtype=np.float32)

    for s_sec, e_sec, pitch, is_drum in synth_guide:
        i0 = max(0, int(s_sec * fs))
        if i0 >= len(y):
            continue
        if is_drum:
            ln = min(int(0.03 * fs), len(y) - i0)
            tt = np.arange(ln, dtype=np.float32) / fs
            f = 75.0 if pitch in (35, 36) else 1200.0
            y[i0 : i0 + ln] += 0.35 * np.sin(2.0 * np.pi * f * tt) * np.exp(-tt * 60.0)
        else:
            dur = max(0.06, min(1.8, e_sec - s_sec))
            ln = min(int(dur * fs), len(y) - i0)
            tt = np.arange(ln, dtype=np.float32) / fs
            f = 440.0 * (2.0 ** ((pitch - 69) / 12.0))
            env = np.minimum(1.0, tt / 0.008) * np.exp(-tt * 3.5)
            sig = 0.10 * (
                np.sin(2.0 * np.pi * f * tt)
                + 0.45 * np.sin(4.0 * np.pi * f * tt)
                + 0.2 * np.sin(6.0 * np.pi * f * tt)
            ) * env
            y[i0 : i0 + ln] += sig.astype(np.float32)

    return y


def _extract_waveform_attack_onsets(audio_path: Path) -> tuple[np.ndarray, float]:
    """Extract 2.9ms-resolution attack-backtracked onsets and first audio transient."""
    import librosa
    from scipy.signal import find_peaks

    y, sr = librosa.load(str(audio_path), sr=44100, mono=True)
    hop = 128
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    env_n = env / (np.percentile(env, 99.5) + 1e-6)
    peaks, _ = find_peaks(env_n, height=0.18, prominence=0.12, distance=int(0.045 * sr / hop))
    bt_frames = librosa.onset.onset_backtrack(peaks, env)
    attack_onsets = librosa.frames_to_time(bt_frames, sr=sr, hop_length=hop)

    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0]
    thresh = float(np.max(rms)) * 0.04
    first_idx = int(np.argmax(rms > thresh)) if np.any(rms > thresh) else 0
    first_transient = float(librosa.frames_to_time(first_idx, sr=sr, hop_length=hop))

    return attack_onsets.astype(np.float64), first_transient


def _snap_hits_to_attacks(drum_hits: list[DrumHit], attack_onsets: np.ndarray) -> np.ndarray:
    """Shift ADTOF detections backward [-65ms, +15ms] onto crisp 2.9ms drum attacks."""
    raw = np.array(sorted(h.time_sec for h in drum_hits if h.drum_class in ("kick", "snare")), dtype=np.float64)
    if len(raw) == 0 or len(attack_onsets) == 0:
        return raw

    snapped = raw.copy()
    for i, t in enumerate(raw):
        idx = int(np.searchsorted(attack_onsets, t))
        cands = []
        if idx > 0:
            cands.append(attack_onsets[idx - 1])
        if idx < len(attack_onsets):
            cands.append(attack_onsets[idx])
        valid = [c for c in cands if -0.065 <= (c - t) <= 0.015]
        if valid:
            snapped[i] = min(valid, key=lambda c: abs(c - t))
    return np.sort(snapped)


def _estimate_isolated_drum_warp(
    m_ks: np.ndarray,
    r_ks: np.ndarray,
    duration_sec: float,
    first_note_t: float = 0.0,
    first_aud_transient: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Sequential constrained drum-warp (used when DTW isn't run; breaks ties toward small lag)."""
    if len(m_ks) < 8 or len(r_ks) < 8:
        return np.array([0.0, duration_sec]), np.array([0.0, duration_sec]), 0.0

    def _best_lag(sub: np.ndarray, grid: np.ndarray, ref_lag: float = 0.0, tol: float = 0.025) -> tuple[float, float]:
        best_l, best_score = float(grid[0]), -1.0
        best_hits = 0
        for lag in grid:
            sh = sub + lag
            idx = np.searchsorted(r_ks, sh)
            d1 = np.abs(r_ks[np.minimum(idx, len(r_ks) - 1)] - sh)
            d0 = np.abs(r_ks[np.maximum(idx - 1, 0)] - sh)
            hits = int(np.sum(np.minimum(d1, d0) <= tol))
            # Tiny proximity tie-breaker prevents periodic 4-beat aliasing
            score = float(hits) - 0.05 * abs(float(lag) - ref_lag)
            if score > best_score:
                best_score, best_l, best_hits = score, float(lag), hits
        return best_l, best_hits / float(max(1, len(sub)))

    first_drum = float(m_ks[0])
    init_sub = m_ks[(m_ks >= first_drum) & (m_ks < first_drum + 16.0)]
    if len(init_sub) < 6:
        init_sub = m_ks[: min(20, len(m_ks))]

    ref0 = max(0.0, first_aud_transient - first_note_t)
    init_lag, _ = _best_lag(init_sub, np.arange(-2.5, 3.505, 0.004), ref_lag=ref0)
    anchors: list[tuple[float, float]] = [(max(0.0, first_note_t), init_lag)]
    cur_lag = init_lag

    t = max(0.0, first_drum)
    while t < duration_sec - 4.0:
        sub = m_ks[(m_ks >= t) & (m_ks < t + 10.0)]
        if len(sub) >= 6:
            grid = np.arange(cur_lag - 0.20, cur_lag + 0.205, 0.004)
            w_lag, w_score = _best_lag(sub, grid, ref_lag=cur_lag)
            if w_score >= 0.65:
                cur_lag = w_lag
                anchors.append((float(np.median(sub)), w_lag))
        t += 6.0

    anchors.append((duration_sec + 2.0, cur_lag))

    raw_xs = np.array([a[0] for a in anchors], dtype=np.float64)
    raw_ys = np.array([a[1] for a in anchors], dtype=np.float64)
    knots_m = np.arange(0.0, duration_sec + 2.0, 1.0)
    raw_lags = np.interp(knots_m, raw_xs, raw_ys)

    smooth_lags = np.convolve(np.pad(raw_lags, (1, 1), mode="edge"), np.ones(3) / 3.0, mode="valid")
    knots_a = knots_m + smooth_lags
    for i in range(1, len(knots_a)):
        dt = float(knots_m[i] - knots_m[i - 1])
        knots_a[i] = max(knots_a[i], knots_a[i - 1] + 0.75 * dt)

    warped = np.interp(m_ks, knots_m, knots_a)
    idx = np.searchsorted(r_ks, warped)
    d1 = np.abs(r_ks[np.minimum(idx, len(r_ks) - 1)] - warped)
    d0 = np.abs(r_ks[np.maximum(idx - 1, 0)] - warped)
    match_rate = float(np.mean(np.minimum(d1, d0) <= 0.030))
    return knots_m, knots_a, match_rate


def _align_hybrid_dtw_plus_isolated_drums(
    main_audio: Path,
    drums_wav: Path,
    synth_guide: list[tuple[float, float, int, bool]],
    midi_drums: list[DrumHit],
    real_drums: list[DrumHit],
    duration_sec: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Combine guitar-aware intro lock, SyncToolbox DTW, and 2-bar isolated drum rush tracking."""
    import librosa
    from synctoolbox.dtw.mrmsdtw import sync_via_mrmsdtw
    from synctoolbox.dtw.utils import compute_optimal_chroma_shift, shift_chroma_vectors
    from synctoolbox.feature.chroma import pitch_to_chroma, quantize_chroma, quantized_chroma_to_CENS
    from synctoolbox.feature.dlnco import pitch_onset_features_to_DLNCO
    from synctoolbox.feature.pitch import audio_to_pitch_features
    from synctoolbox.feature.pitch_onset import audio_to_pitch_onset_features
    from synctoolbox.feature.utils import estimate_tuning

    drum_attacks, _ = _extract_waveform_attack_onsets(drums_wav)
    _, first_mix_transient = _extract_waveform_attack_onsets(main_audio)
    r_ks = _snap_hits_to_attacks(real_drums, drum_attacks)
    m_ks = np.array(sorted(h.time_sec for h in midi_drums if h.drum_class in ("kick", "snare")), dtype=np.float64)

    # 1. Check Studio Click Lock first (for click-tracked metal like B.Y.O.B.)
    if len(m_ks) >= 24 and len(r_ks) >= 24:
        best_g_lag, best_g_hits = 0.0, -1
        for lag in np.arange(-2.0, 3.005, 0.004):
            sh = m_ks + lag
            idx = np.searchsorted(r_ks, sh)
            d1 = np.abs(r_ks[np.minimum(idx, len(r_ks) - 1)] - sh)
            d0 = np.abs(r_ks[np.maximum(idx - 1, 0)] - sh)
            c = int(np.sum(np.minimum(d1, d0) <= 0.035))
            if c > best_g_hits:
                best_g_hits, best_g_lag = c, float(lag)

        g_rate = best_g_hits / float(len(m_ks))
        if g_rate >= 0.72:
            third = max(10.0, duration_sec / 3.0)
            slice_rates = []
            for s0 in (0.0, third, 2.0 * third):
                sub = m_ks[(m_ks >= s0) & (m_ks < s0 + third)]
                if len(sub) >= 8:
                    sh = sub + best_g_lag
                    idx = np.searchsorted(r_ks, sh)
                    d1 = np.abs(r_ks[np.minimum(idx, len(r_ks) - 1)] - sh)
                    d0 = np.abs(r_ks[np.maximum(idx - 1, 0)] - sh)
                    slice_rates.append(float(np.mean(np.minimum(d1, d0) <= 0.035)))
            if slice_rates and min(slice_rates) >= 0.65:
                print(f"[TaikoLab TabSync] Studio Click Lock detected (lag={best_g_lag:+.3f}s, match={g_rate*100:.1f}%) -> Skipping DTW warp!")
                knots_m = np.array([0.0, duration_sec + 2.0], dtype=np.float64)
                knots_a = knots_m + best_g_lag
                return knots_m, knots_a

    # 2. Run SyncToolbox DTW for robust macro path
    Fs = 22050
    feature_rate = 50

    y_aud, _ = librosa.load(str(main_audio), sr=Fs, mono=True)
    y_mid = _synthesize_midi_guide(synth_guide, duration_sec, fs=Fs)

    def _feats(y: np.ndarray):
        tuning = estimate_tuning(y, Fs)
        f_pitch = audio_to_pitch_features(f_audio=y, Fs=Fs, tuning_offset=tuning, feature_rate=feature_rate)
        f_chroma = pitch_to_chroma(f_pitch=f_pitch)
        f_q = quantize_chroma(f_chroma=f_chroma)
        f_cens, _ = quantized_chroma_to_CENS(f_q, 21, 5, feature_rate)
        f_onset = audio_to_pitch_onset_features(f_audio=y, Fs=Fs, tuning_offset=tuning)
        f_dlnco = pitch_onset_features_to_DLNCO(
            f_peaks=f_onset,
            feature_rate=feature_rate,
            feature_sequence_length=f_chroma.shape[1],
        )
        return f_chroma, f_cens, f_dlnco

    print("[TaikoLab TabSync] Running SyncToolbox Macro DTW + 2-Bar Isolated Drum Rush Tracking...")
    ch1, cens1, dl1 = _feats(y_mid)
    ch2, cens2, dl2 = _feats(y_aud)
    shift = compute_optimal_chroma_shift(cens1, cens2)
    ch1 = shift_chroma_vectors(ch1, shift)
    dl1 = shift_chroma_vectors(dl1, shift)

    wp = sync_via_mrmsdtw(
        f_chroma1=ch1,
        f_chroma2=ch2,
        f_onset1=dl1,
        f_onset2=dl2,
        input_feature_rate=feature_rate,
        step_weights=np.array([1.5, 1.5, 2.0]),
        threshold_rec=10**6,
    )
    t_mid_wp = wp[0].astype(np.float64) / float(feature_rate)
    t_aud_wp = wp[1].astype(np.float64) / float(feature_rate)

    first_note_t = min((n[0] for n in synth_guide), default=0.0)
    first_drum_t = float(m_ks[0]) if len(m_ks) > 0 else first_note_t
    last_note_t = max((n[0] for n in synth_guide), default=duration_sec)

    # 3. Dual-Stage Intro Lock:
    # Guitar intro (t < first_drum_t) locks directly to opening guitar transient
    guitar_intro_lag = max(0.0, first_mix_transient - first_note_t)
    drum_intro_lag = guitar_intro_lag

    if len(m_ks) >= 6 and len(r_ks) >= 6:
        early_m = m_ks[m_ks < min(first_drum_t + 16.0, m_ks[0] + 16.0)]
        if len(early_m) >= 6:
            best_l, best_c = guitar_intro_lag, -1
            for lag in np.arange(guitar_intro_lag - 0.15, guitar_intro_lag + 0.155, 0.003):
                sh = early_m + lag
                idx = np.searchsorted(r_ks, sh)
                d1 = np.abs(r_ks[np.minimum(idx, len(r_ks) - 1)] - sh)
                d0 = np.abs(r_ks[np.maximum(idx - 1, 0)] - sh)
                c = int(np.sum(np.minimum(d1, d0) <= 0.024))
                if c > best_c:
                    best_c, best_l = c, float(lag)
            if best_c / float(len(early_m)) >= 0.65:
                drum_intro_lag = best_l

    print(f"[TaikoLab TabSync] Intro lock -> Guitar={guitar_intro_lag:+.3f}s, Drums={drum_intro_lag:+.3f}s")

    # Build 0.5s fine knot grid (prevents wide 5s smoothing from blurring over 2-bar drummer rushes!)
    knots_m = np.arange(0.0, last_note_t + 2.0, 0.5)
    raw_dtw_a = np.interp(knots_m, t_mid_wp, t_aud_wp)
    knots_a = np.zeros_like(knots_m)

    for i, tm in enumerate(knots_m):
        if tm <= first_note_t + 0.5:
            knots_a[i] = tm + guitar_intro_lag
        elif tm <= first_drum_t + 1.0:
            # Smoothly transition from guitar intro lag to drum intro lag
            alpha = (tm - first_note_t) / max(0.5, first_drum_t + 1.0 - first_note_t)
            knots_a[i] = tm + (1.0 - alpha) * guitar_intro_lag + alpha * drum_intro_lag
        else:
            cand_a = float(raw_dtw_a[i])
            if i > 0:
                dt = float(knots_m[i] - knots_m[i - 1])
                cand_a = max(cand_a, knots_a[i - 1] + 0.75 * dt)
            knots_a[i] = cand_a

    # 4. Fast 2-Bar (2.4s window) Drum-Rush Tracking against isolated drum attacks
    if len(m_ks) >= 10 and len(r_ks) >= 10:
        nudge_grid = np.arange(-0.160, 0.161, 0.003)
        snap_knots_t: list[float] = [0.0, first_drum_t]
        snap_knots_lag: list[float] = [guitar_intro_lag, drum_intro_lag]

        # Step in fast 2-bar increments (2.4s at ~200 BPM)
        for win_start in np.arange(first_drum_t + 0.5, last_note_t - 2.0, 2.4):
            mask_m = (m_ks >= win_start) & (m_ks < win_start + 4.8)
            if int(np.sum(mask_m)) < 5:
                continue
            base_warped = np.interp(m_ks[mask_m], knots_m, knots_a)
            best_n, best_score = 0.0, -1.0
            best_hits = 0
            for ng in nudge_grid:
                sh = base_warped + ng
                idx = np.searchsorted(r_ks, sh)
                d1 = np.abs(r_ks[np.minimum(idx, len(r_ks) - 1)] - sh)
                d0 = np.abs(r_ks[np.maximum(idx - 1, 0)] - sh)
                hits = int(np.sum(np.minimum(d1, d0) <= 0.024))
                score = float(hits) - 0.08 * abs(float(ng))
                if score > best_score:
                    best_score, best_n, best_hits = score, float(ng), hits

            conf = best_hits / float(np.sum(mask_m))
            if conf >= 0.58:
                mid_t = float(win_start + 2.4)
                base_lag = float(np.interp(mid_t, knots_m, knots_a) - mid_t)
                snap_knots_t.append(mid_t)
                snap_knots_lag.append(base_lag + best_n)

        if len(snap_knots_t) >= 4:
            snap_knots_t.append(knots_m[-1])
            snap_knots_lag.append(snap_knots_lag[-1])
            interp_lags = np.interp(knots_m, snap_knots_t, snap_knots_lag)
            # Tight 3-point smoothing (retains 2-bar rushes while stopping single-note jitter)
            smooth_lags = np.convolve(np.pad(interp_lags, (1, 1), mode="edge"), np.ones(3) / 3.0, mode="valid")
            for i, tm in enumerate(knots_m):
                if tm <= first_note_t + 0.5:
                    smooth_lags[i] = guitar_intro_lag
            knots_a = knots_m + smooth_lags
            for i in range(1, len(knots_a)):
                dt = float(knots_m[i] - knots_m[i - 1])
                knots_a[i] = max(knots_a[i], knots_a[i - 1] + 0.75 * dt)

        final_warped = np.interp(m_ks, knots_m, knots_a)
        idx = np.searchsorted(r_ks, final_warped)
        d1 = np.abs(r_ks[np.minimum(idx, len(r_ks) - 1)] - final_warped)
        d0 = np.abs(r_ks[np.maximum(idx - 1, 0)] - final_warped)
        match_pct = 100.0 * float(np.mean(np.minimum(d1, d0) <= 0.025))
        signed = [
            r_ks[int(np.argmin(np.abs(r_ks - t)))] - t
            for t in final_warped
            if abs(r_ks[int(np.argmin(np.abs(r_ks - t)))] - t) <= 0.050
        ]
        med_err_ms = float(np.median(signed)) * 1000.0 if signed else 0.0
        print(f"[TaikoLab TabSync] Fast 2-Bar Drum Rush Lock: {match_pct:.1f}% within +/-25ms (median={med_err_ms:+.1f}ms)")

    return knots_m, knots_a

    print("[TaikoLab TabSync] Running SyncToolbox Macro DTW + Isolated Drum Refinement...")
    ch1, cens1, dl1 = _feats(y_mid)
    ch2, cens2, dl2 = _feats(y_aud)
    shift = compute_optimal_chroma_shift(cens1, cens2)
    ch1 = shift_chroma_vectors(ch1, shift)
    dl1 = shift_chroma_vectors(dl1, shift)

    wp = sync_via_mrmsdtw(
        f_chroma1=ch1,
        f_chroma2=ch2,
        f_onset1=dl1,
        f_onset2=dl2,
        input_feature_rate=feature_rate,
        step_weights=np.array([1.5, 1.5, 2.0]),
        threshold_rec=10**6,
    )
    t_mid_wp = wp[0].astype(np.float64) / float(feature_rate)
    t_aud_wp = wp[1].astype(np.float64) / float(feature_rate)



    first_note_t = min((n[0] for n in synth_guide), default=0.0)
    last_note_t = max((n[0] for n in synth_guide), default=duration_sec)

    # Lock Beat 1 cleanly from first audio transient + earliest isolated drums
    intro_lag = first_mix_transient - first_note_t
    if len(m_ks) >= 6 and len(r_ks) >= 6:
        early_m = m_ks[m_ks < min(20.0, m_ks[0] + 15.0)]
        if len(early_m) >= 6:
            best_l, best_c = intro_lag, -1
            for lag in np.arange(intro_lag - 0.15, intro_lag + 0.155, 0.003):
                sh = early_m + lag
                idx = np.searchsorted(r_ks, sh)
                d1 = np.abs(r_ks[np.minimum(idx, len(r_ks) - 1)] - sh)
                d0 = np.abs(r_ks[np.maximum(idx - 1, 0)] - sh)
                c = int(np.sum(np.minimum(d1, d0) <= 0.024))
                if c > best_c:
                    best_c, best_l = c, float(lag)
            if best_c / float(len(early_m)) >= 0.70:
                intro_lag = best_l

    print(f"[TaikoLab TabSync] Locked Beat-1 intro lag: {intro_lag:+.3f}s (first transient={first_mix_transient:.3f}s)")

    # Construct slope-protected DTW knot grid (fixes sparse intro freeze)
    first_drum_t = float(m_ks[0]) if len(m_ks) > 0 else first_note_t
    hold_until_t = min(last_note_t, max(first_note_t + 8.0, first_drum_t + 6.0))
    anchor_out_t = max(hold_until_t + 1.0, last_note_t - 2.0)

    knots_m = np.arange(0.0, last_note_t + 2.0, 1.0)
    raw_dtw_a = np.interp(knots_m, t_mid_wp, t_aud_wp)
    knots_a = np.zeros_like(knots_m)

    last_good_lag = intro_lag
    for i, tm in enumerate(knots_m):
        if tm <= hold_until_t:
            knots_a[i] = tm + intro_lag
            last_good_lag = intro_lag
        elif tm > anchor_out_t:
            knots_a[i] = max(knots_a[i - 1] + 0.75, tm + last_good_lag)
        else:
            cand_a = float(raw_dtw_a[i])
            cand_lag = cand_a - float(tm)
            if abs(cand_lag - last_good_lag) > 0.35:
                cand_a = float(tm) + last_good_lag
            else:
                last_good_lag = cand_lag
            if i > 0:
                dt = float(knots_m[i] - knots_m[i - 1])
                cand_a = max(cand_a, knots_a[i - 1] + 0.75 * dt)
            knots_a[i] = cand_a

    # High-confidence (+/- 140ms) snap onto ISOLATED Demucs drum attacks per 4s window
    if len(m_ks) >= 10 and len(r_ks) >= 10:
        nudge_grid = np.arange(-0.140, 0.141, 0.004)
        snap_knots_t: list[float] = [0.0]
        snap_knots_lag: list[float] = [intro_lag]

        for win_start in np.arange(first_drum_t, last_note_t - 2.0, 4.0):
            mask_m = (m_ks >= win_start) & (m_ks < win_start + 7.0)
            if int(np.sum(mask_m)) < 6:
                continue
            base_warped = np.interp(m_ks[mask_m], knots_m, knots_a)
            best_n, best_score = 0.0, -1.0
            best_hits = 0
            for ng in nudge_grid:
                sh = base_warped + ng
                idx = np.searchsorted(r_ks, sh)
                d1 = np.abs(r_ks[np.minimum(idx, len(r_ks) - 1)] - sh)
                d0 = np.abs(r_ks[np.maximum(idx - 1, 0)] - sh)
                hits = int(np.sum(np.minimum(d1, d0) <= 0.022))
                score = float(hits) - 0.15 * abs(float(ng))
                if score > best_score:
                    best_score, best_n, best_hits = score, float(ng), hits

            conf = best_hits / float(np.sum(mask_m))
            if conf >= 0.65:
                mid_t = float(win_start + 3.0)
                base_lag = float(np.interp(mid_t, knots_m, knots_a) - mid_t)
                snap_knots_t.append(mid_t)
                snap_knots_lag.append(base_lag + best_n)

        if len(snap_knots_t) >= 3:
            snap_knots_t.append(knots_m[-1])
            snap_knots_lag.append(snap_knots_lag[-1])
            interp_lags = np.interp(knots_m, snap_knots_t, snap_knots_lag)
            smooth_lags = np.convolve(np.pad(interp_lags, (2, 2), mode="edge"), np.ones(5) / 5.0, mode="valid")
            for i, tm in enumerate(knots_m):
                if tm <= first_note_t + 2.0:
                    smooth_lags[i] = intro_lag
            knots_a = knots_m + smooth_lags
            for i in range(1, len(knots_a)):
                dt = float(knots_m[i] - knots_m[i - 1])
                knots_a[i] = max(knots_a[i], knots_a[i - 1] + 0.75 * dt)

        final_warped = np.interp(m_ks, knots_m, knots_a)
        idx = np.searchsorted(r_ks, final_warped)
        d1 = np.abs(r_ks[np.minimum(idx, len(r_ks) - 1)] - final_warped)
        d0 = np.abs(r_ks[np.maximum(idx - 1, 0)] - final_warped)
        match_pct = 100.0 * float(np.mean(np.minimum(d1, d0) <= 0.030))
        signed = [
            r_ks[int(np.argmin(np.abs(r_ks - t)))] - t
            for t in final_warped
            if abs(r_ks[int(np.argmin(np.abs(r_ks - t)))] - t) <= 0.060
        ]
        med_err_ms = float(np.median(signed)) * 1000.0 if signed else 0.0
        print(f"[TaikoLab TabSync] Hybrid DTW + Isolated Drum Lock: {match_pct:.1f}% within +/-30ms (signed bias={med_err_ms:+.1f}ms)")

    return knots_m, knots_a


def _split_double_time_bars(
    bars: list[tuple[float, float, float, int, int]],
    drums: list[DrumHit],
) -> list[tuple[float, float, float, int, int]]:
    """Split slow-notated 4/4 bars (<130 BPM) containing 32nd-note thrash streams into 2x BPM."""
    core_times = sorted(h.time_sec for h in drums if h.drum_class in ("kick", "snare", "tom", "crash"))
    out: list[tuple[float, float, float, int, int]] = []

    for start, end, bpm, num, den in bars:
        if num == 4 and den == 4 and bpm < 130.0:
            bar_hits = [t for t in core_times if start - 0.01 <= t < end - 0.01]
            gaps = [bar_hits[i + 1] - bar_hits[i] for i in range(len(bar_hits) - 1)]
            fast_gaps = [g for g in gaps if 0.035 <= g <= 0.105]
            if len(bar_hits) >= 8 and len(fast_gaps) >= 3:
                mid = 0.5 * (start + end)
                out.append((start, mid, min(360.0, bpm * 2.0), 4, 4))
                out.append((mid, end, min(360.0, bpm * 2.0), 4, 4))
                continue
        out.append((start, end, bpm, num, den))
    return out


def import_midi_aligned(
    song_id: str,
    song_dir: Path,
    midi_path: Path,
    title: str | None = None,
    artist: str = "Unknown",
    manual_offset_sec: float | None = None,
    device: str = "cuda",
) -> SongAnalysis:
    """Parse a MIDI tab and lock it to real audio via SyncToolbox DTW + HTDemucs Isolated Drums."""
    if not midi_path.exists():
        raise FileNotFoundError(f"MIDI file not found: {midi_path}")

    raw_main_audio, stems = discover_song_files(song_dir)
    main_audio = _ensure_clean_simulator_audio(song_dir, raw_main_audio)
    duration = get_audio_duration(main_audio)

    if not stems.drums:
        stems = separate_stems_demucs(song_dir, main_audio, device=device)

    bars, drums, vocals, guitars, synth_guide = _parse_midi_unified(midi_path)
    print(
        f"[TaikoLab TabSync] Parsed MIDI: {len(bars)} bars | "
        f"{len(drums)} drums | {len(vocals)} vocals | {len(guitars)} guitar strokes"
    )

    if manual_offset_sec is not None:
        knots_m = np.array([0.0, duration], dtype=np.float64)
        knots_a = knots_m + manual_offset_sec
        print(f"[TaikoLab TabSync] Manual offset override: {manual_offset_sec:+.3f}s")
    else:
        from taikolab.ingest.drum_transcriber import transcribe_drums_hybrid

        drums_wav = Path(stems.drums) if stems.drums else main_audio
        real_drums = transcribe_drums_hybrid(
            drums_wav,
            has_isolated_drums=bool(stems.drums),
            device=device,
        )
        knots_m, knots_a = _align_hybrid_dtw_plus_isolated_drums(
            main_audio=main_audio,
            drums_wav=drums_wav,
            synth_guide=synth_guide,
            midi_drums=drums,
            real_drums=real_drums,
            duration_sec=duration,
        )

    def warp(t: float) -> float:
        return float(np.interp(t, knots_m, knots_a))

    warped_drums = [
        DrumHit(time_sec=round(wt, 4), drum_class=h.drum_class, velocity=h.velocity, source="tab")
        for h in drums
        if 0.0 <= (wt := warp(h.time_sec)) <= duration
    ]
    warped_vocals = [
        OnsetEvent(time_sec=round(wt, 4), strength=v.strength, duration_sec=v.duration_sec, pitch_hz=v.pitch_hz)
        for v in vocals
        if 0.0 <= (wt := warp(v.time_sec)) <= duration
    ]
    warped_guitars = [
        OnsetEvent(time_sec=round(wt, 4), strength=g.strength)
        for g in guitars
        if 0.0 <= (wt := warp(g.time_sec)) <= duration
    ]

    warped_bars: list[tuple[float, float, float, int, int]] = []
    for s, e, _, num, den in bars:
        ws = warp(s)
        we = warp(e)
        if we <= 0.05 or ws >= duration - 0.1:
            continue
        if ws < 0.0:
            ws = 0.0
        we = min(duration, we)
        if we - ws >= 0.30:
            wbpm = max(30.0, min(360.0, 240.0 * num / (den * (we - ws))))
            warped_bars.append((ws, we, wbpm, num, den))

    warped_bars = _split_double_time_bars(warped_bars, warped_drums)

    beats: list[BeatEvent] = []
    regions: list[TempoRegion] = []
    for ws, we, wbpm, num, den in warped_bars:
        beats.append(BeatEvent(time_sec=round(ws, 4), is_downbeat=True))
        n_beats = 2 if (num == 6 and den == 8) else num
        step = (we - ws) / max(1, n_beats)
        for b in range(1, n_beats):
            beats.append(BeatEvent(time_sec=round(ws + b * step, 4), is_downbeat=False))

        if regions and regions[-1].numerator == num and regions[-1].denominator == den and abs(regions[-1].bpm - wbpm) <= 3.5:
            regions[-1].end_sec = round(we, 4)
        else:
            regions.append(
                TempoRegion(
                    start_sec=round(ws, 4),
                    end_sec=round(we, 4),
                    bpm=round(wbpm, 2),
                    numerator=num,
                    denominator=den,
                )
            )

    analysis = SongAnalysis(
        song_id=song_id,
        title=title or song_id.replace("_", " ").title(),
        artist=artist,
        audio_path=main_audio.name,
        duration_sec=round(duration, 3),
        stems=stems,
        beats=beats,
        tempo_regions=regions,
        drum_hits=warped_drums,
        vocal_events=warped_vocals,
        guitar_events=warped_guitars,
    )

    out_json = song_dir / "analysis.json"
    out_json.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    print(f"[TaikoLab TabSync] Saved DTW+Demucs analysis -> {out_json} ({len(warped_bars)} measures, {len(regions)} regions)")
    return analysis