"""Pure-mido MIDI Ingest with Band-Wide SyncToolbox Chroma+Onset DTW Alignment."""

from __future__ import annotations

from pathlib import Path

import mido
import numpy as np
import soundfile as sf

from taikolab.ingest.stem_loader import discover_song_files, get_audio_duration
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
    all_notes: list[tuple[float, float, int, int, int, str]] = []  # (start_s, end_s, ch, pitch, vel, track_name)

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
    synth_guide: list[tuple[float, float, int, bool]] = []  # (start, end, pitch, is_drum)

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


def _count_monotonic_onset_matches(
    expected_times: np.ndarray,
    audio_times: np.ndarray,
    tolerance_sec: float,
) -> int:
    return _score_monotonic_onset_matches(expected_times, audio_times, tolerance_sec)[0]


def _score_monotonic_onset_matches(
    expected_times: np.ndarray,
    audio_times: np.ndarray,
    tolerance_sec: float,
) -> tuple[int, float]:
    expected = np.sort(np.asarray(expected_times, dtype=np.float64))
    actual = np.sort(np.asarray(audio_times, dtype=np.float64))
    expected_idx = 0
    actual_idx = 0
    matches = 0
    absolute_error = 0.0

    while expected_idx < len(expected) and actual_idx < len(actual):
        delta = actual[actual_idx] - expected[expected_idx]
        if delta < -tolerance_sec:
            actual_idx += 1
        elif delta > tolerance_sec:
            expected_idx += 1
        else:
            matches += 1
            absolute_error += abs(float(delta))
            expected_idx += 1
            actual_idx += 1

    return matches, absolute_error


def _search_affine_drum_warp(
    midi_time_groups: list[np.ndarray],
    audio_time_groups: list[np.ndarray],
    duration_sec: float,
    anchor_midi_sec: float,
    anchor_audio_sec: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    groups = [
        (np.sort(np.asarray(midi_times, dtype=np.float64)), np.sort(np.asarray(audio_times, dtype=np.float64)))
        for midi_times, audio_times in zip(midi_time_groups, audio_time_groups)
        if len(midi_times) >= 4 and len(audio_times) >= 4
    ]
    initial_lag = anchor_audio_sec - anchor_midi_sec

    def score(rate: float, lag: float) -> tuple[int, float]:
        scores = [
            _score_monotonic_onset_matches(
                midi_times + lag + (rate - 1.0) * (midi_times - anchor_midi_sec),
                audio_times,
                tolerance_sec=0.060,
            )
            for midi_times, audio_times in groups
        ]
        return sum(matches for matches, _ in scores), sum(error for _, error in scores)

    best_rate = 1.0
    best_lag = initial_lag
    best_score = -1
    best_error = float("inf")

    def consider(rate: float, lag: float) -> None:
        nonlocal best_rate, best_lag, best_score, best_error
        candidate_score, candidate_error = score(rate, lag)
        if (
            candidate_score > best_score
            or (
                candidate_score == best_score
                and (
                    candidate_error < best_error - 1e-9
                    or (
                        abs(candidate_error - best_error) <= 1e-9
                        and (abs(rate - 1.0), abs(lag - initial_lag))
                        < (abs(best_rate - 1.0), abs(best_lag - initial_lag))
                    )
                )
            )
        ):
            best_rate = rate
            best_lag = lag
            best_score = candidate_score
            best_error = candidate_error

    for rate in np.arange(0.90, 1.1001, 0.002):
        for lag in np.arange(initial_lag - 0.25, initial_lag + 0.2501, 0.01):
            consider(float(rate), float(lag))

    coarse_rate = best_rate
    coarse_lag = best_lag
    for rate in np.arange(max(0.90, coarse_rate - 0.002), min(1.10, coarse_rate + 0.0021), 0.0001):
        for lag in np.arange(coarse_lag - 0.01, coarse_lag + 0.0101, 0.001):
            consider(float(rate), float(lag))

    end_time = max(float(duration_sec), 0.1)
    knots_m = np.array([0.0, end_time], dtype=np.float64)
    knots_a = knots_m + best_lag + (best_rate - 1.0) * (knots_m - anchor_midi_sec)
    return knots_m, knots_a, best_rate


def _estimate_isolated_drum_warp(
    midi_times: np.ndarray,
    audio_times: np.ndarray,
    duration_sec: float,
    first_note_t: float,
    first_aud_transient: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Estimate a smooth drum-to-audio clock from corresponding onset sequences."""
    return _search_affine_drum_warp(
        [np.asarray(midi_times, dtype=np.float64)],
        [np.asarray(audio_times, dtype=np.float64)],
        duration_sec=duration_sec,
        anchor_midi_sec=first_note_t,
        anchor_audio_sec=first_aud_transient,
    )


def _estimate_classified_drum_warp(
    midi_drums: list[DrumHit],
    real_drums: list[DrumHit],
    duration_sec: float,
    anchor_midi_sec: float,
    anchor_audio_sec: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    classes = ("kick", "snare")
    midi_groups = [
        np.array([h.time_sec for h in midi_drums if h.drum_class == drum_class], dtype=np.float64)
        for drum_class in classes
    ]
    audio_groups = [
        np.array([h.time_sec for h in real_drums if h.drum_class == drum_class], dtype=np.float64)
        for drum_class in classes
    ]
    return _search_affine_drum_warp(
        midi_groups,
        audio_groups,
        duration_sec=duration_sec,
        anchor_midi_sec=anchor_midi_sec,
        anchor_audio_sec=anchor_audio_sec,
    )


def _score_drum_warp(
    midi_drums: list[DrumHit],
    real_drums: list[DrumHit],
    knots_m: np.ndarray,
    knots_a: np.ndarray,
    tolerance_sec: float = 0.035,
) -> tuple[int, int]:
    matches = 0
    total = 0
    for drum_class in ("kick", "snare"):
        midi_times = np.array(
            sorted(h.time_sec for h in midi_drums if h.drum_class == drum_class),
            dtype=np.float64,
        )
        audio_times = np.array(
            sorted(h.time_sec for h in real_drums if h.drum_class == drum_class),
            dtype=np.float64,
        )
        if not len(midi_times):
            continue
        expected = np.interp(midi_times, knots_m, knots_a)
        matches += _count_monotonic_onset_matches(expected, audio_times, tolerance_sec)
        total += len(midi_times)
    return matches, total


def _select_drum_alignment_warp(
    midi_drums: list[DrumHit],
    real_drums: list[DrumHit],
    duration_sec: float,
    dtw_knots_m: np.ndarray,
    dtw_knots_a: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, int, int, float | None]:
    current_matches, total_hits = _score_drum_warp(
        midi_drums,
        real_drums,
        dtw_knots_m,
        dtw_knots_a,
    )
    if total_hits < 30:
        return dtw_knots_m, dtw_knots_a, current_matches, total_hits, None

    anchor_midi_sec = min(
        h.time_sec for h in midi_drums if h.drum_class in ("kick", "snare")
    )
    anchor_audio_sec = float(np.interp(anchor_midi_sec, dtw_knots_m, dtw_knots_a))
    affine_m, affine_a, affine_rate = _estimate_classified_drum_warp(
        midi_drums,
        real_drums,
        duration_sec=duration_sec,
        anchor_midi_sec=anchor_midi_sec,
        anchor_audio_sec=anchor_audio_sec,
    )
    affine_matches, _ = _score_drum_warp(midi_drums, real_drums, affine_m, affine_a)
    required_improvement = max(12, int(np.ceil(total_hits * 0.05)))
    if affine_matches >= current_matches + required_improvement:
        return affine_m, affine_a, affine_matches, total_hits, affine_rate
    return dtw_knots_m, dtw_knots_a, current_matches, total_hits, None


def _align_via_synctoolbox(
    audio_path: Path,
    synth_guide: list[tuple[float, float, int, bool]],
    midi_drums: list[DrumHit],
    real_drums: list[DrumHit],
    duration_sec: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Run band-wide SyncToolbox Chroma+DLNCO MrMsDTW, fix (0,0) pin, and refine on drum peaks."""
    import librosa
    from synctoolbox.dtw.mrmsdtw import sync_via_mrmsdtw
    from synctoolbox.dtw.utils import compute_optimal_chroma_shift, shift_chroma_vectors
    from synctoolbox.feature.chroma import pitch_to_chroma, quantize_chroma, quantized_chroma_to_CENS
    from synctoolbox.feature.dlnco import pitch_onset_features_to_DLNCO
    from synctoolbox.feature.pitch import audio_to_pitch_features
    from synctoolbox.feature.pitch_onset import audio_to_pitch_onset_features
    from synctoolbox.feature.utils import estimate_tuning

    Fs = 22050
    feature_rate = 50

    y_aud, _ = librosa.load(str(audio_path), sr=Fs, mono=True)
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

    print("[TaikoLab TabSync] Running SyncToolbox Chroma+Onset DTW alignment...")
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

    # Sample warp at 1.0s control knots and fix the artificial (0,0) DTW pin at intro/outro
    first_note_t = min((n[0] for n in synth_guide), default=0.0)
    last_note_t = max((n[0] for n in synth_guide), default=duration_sec)
    anchor_in_t = min(last_note_t, first_note_t + 2.5)
    anchor_out_t = max(anchor_in_t + 1.0, last_note_t - 2.0)

    knots_m = np.arange(0.0, last_note_t + 2.0, 1.0)
    knots_a = np.interp(knots_m, t_mid_wp, t_aud_wp)

    lag_in = float(np.interp(anchor_in_t, t_mid_wp, t_aud_wp) - anchor_in_t)
    lag_out = float(np.interp(anchor_out_t, t_mid_wp, t_aud_wp) - anchor_out_t)
    for i, tm in enumerate(knots_m):
        if tm < anchor_in_t:
            knots_a[i] = tm + lag_in
        elif tm > anchor_out_t:
            knots_a[i] = tm + lag_out

    # Gentle +/- 40ms fine-snap on ADTOF drum transients per 6s window, keeping monotonic smoothness
    m_ks = np.array(sorted(h.time_sec for h in midi_drums if h.drum_class in ("kick", "snare")), dtype=np.float64)
    r_ks = np.array(sorted(h.time_sec for h in real_drums if h.drum_class in ("kick", "snare")), dtype=np.float64)

    if len(m_ks) >= 10 and len(r_ks) >= 10:
        nudge_grid = np.arange(-0.045, 0.046, 0.005)
        refined_a = knots_a.copy()
        for win_start in np.arange(first_note_t, last_note_t, 6.0):
            mask_m = (m_ks >= win_start) & (m_ks < win_start + 8.0)
            if int(np.sum(mask_m)) < 6:
                continue
            base_warped = np.interp(m_ks[mask_m], knots_m, knots_a)
            best_n, best_c = 0.0, -1
            for ng in nudge_grid:
                sh = base_warped + ng
                idx = np.searchsorted(r_ks, sh)
                d1 = np.abs(r_ks[np.minimum(idx, len(r_ks) - 1)] - sh)
                d0 = np.abs(r_ks[np.maximum(idx - 1, 0)] - sh)
                c = int(np.sum(np.minimum(d1, d0) <= 0.025))
                if c > best_c:
                    best_c, best_n = c, float(ng)
            if best_c / float(np.sum(mask_m)) >= 0.60:
                k_mask = (knots_m >= win_start) & (knots_m < win_start + 6.0)
                refined_a[k_mask] += best_n

        # 5-point moving average on lags so bar tempos never jitter wildly
        lags = refined_a - knots_m
        kernel = np.ones(5) / 5.0
        smooth_lags = np.convolve(np.pad(lags, (2, 2), mode="edge"), kernel, mode="valid")
        knots_a = np.maximum.accumulate(knots_m + smooth_lags)

        final_warped = np.interp(m_ks, knots_m, knots_a)
        idx = np.searchsorted(r_ks, final_warped)
        d1 = np.abs(r_ks[np.minimum(idx, len(r_ks) - 1)] - final_warped)
        d0 = np.abs(r_ks[np.maximum(idx - 1, 0)] - final_warped)
        match_pct = 100.0 * float(np.mean(np.minimum(d1, d0) <= 0.035))
        print(f"[TaikoLab TabSync] DTW + Drum Snap locked {match_pct:.1f}% of all Kick/Snare hits within +/-35ms!")

    dtw_matches, total_hits = _score_drum_warp(midi_drums, real_drums, knots_m, knots_a)
    knots_m, knots_a, selected_matches, total_hits, affine_rate = _select_drum_alignment_warp(
        midi_drums,
        real_drums,
        duration_sec,
        knots_m,
        knots_a,
    )
    if affine_rate is not None:
        print(
            "[TaikoLab TabSync] Replaced unstable DTW warp with drum-validated affine timing "
            f"({dtw_matches}/{total_hits} -> {selected_matches}/{total_hits} hits within 35ms; "
            f"rate {affine_rate:.5f})."
        )

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
            has_sustained_fast_run = False
            run_start_idx: int | None = None
            for gap_idx, gap in enumerate(gaps):
                if 0.035 <= gap <= 0.105:
                    if run_start_idx is None:
                        run_start_idx = gap_idx
                elif run_start_idx is not None:
                    run_gaps = gap_idx - run_start_idx
                    run_span = bar_hits[gap_idx] - bar_hits[run_start_idx]
                    if run_gaps >= 7 and run_span >= (end - start) * 0.5:
                        has_sustained_fast_run = True
                    run_start_idx = None

            if run_start_idx is not None:
                run_gaps = len(gaps) - run_start_idx
                run_span = bar_hits[-1] - bar_hits[run_start_idx]
                if run_gaps >= 7 and run_span >= (end - start) * 0.5:
                    has_sustained_fast_run = True

            if (
                len(bar_hits) >= 8
                and has_sustained_fast_run
            ):
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
    """Parse a MIDI tab and align it to real audio via SyncToolbox Chroma+Onset DTW."""
    if not midi_path.exists():
        raise FileNotFoundError(f"MIDI file not found: {midi_path}")

    raw_main_audio, stems = discover_song_files(song_dir)
    main_audio = _ensure_clean_simulator_audio(song_dir, raw_main_audio)
    duration = get_audio_duration(main_audio)

    bars, drums, vocals, guitars, synth_guide = _parse_midi_unified(midi_path)
    print(
        f"[TaikoLab TabSync] Parsed MIDI (pure mido): {len(bars)} bars | "
        f"{len(drums)} drums | {len(vocals)} vocals | {len(guitars)} guitar strokes"
    )
    bars = _split_double_time_bars(bars, drums)

    if manual_offset_sec is not None:
        knots_m = np.array([0.0, duration], dtype=np.float64)
        knots_a = knots_m + manual_offset_sec
        print(f"[TaikoLab TabSync] Manual offset override: {manual_offset_sec:+.3f}s")
    else:
        if stems.drums:
            from taikolab.ingest.drum_dsp import extract_dsp_drum_hits

            real_drums = extract_dsp_drum_hits(Path(stems.drums))
        else:
            from taikolab.ingest.drum_transcriber import run_adtof_transcription

            real_drums = run_adtof_transcription(main_audio, device=device)

        knots_m, knots_a = _align_via_synctoolbox(main_audio, synth_guide, drums, real_drums, duration)

    def warp(t: float) -> float:
        if len(knots_m) > 1 and t < knots_m[0]:
            slope = (knots_a[1] - knots_a[0]) / (knots_m[1] - knots_m[0])
            return float(knots_a[0] + (t - knots_m[0]) * slope)
        if len(knots_m) > 1 and t > knots_m[-1]:
            slope = (knots_a[-1] - knots_a[-2]) / (knots_m[-1] - knots_m[-2])
            return float(knots_a[-1] + (t - knots_m[-1]) * slope)
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

    warped_bars: list[tuple[float, float, float, int, int, float, float]] = []
    for s, e, _, num, den in bars:
        warped_start = warp(s)
        warped_end = warp(e)
        if warped_end <= 0.05 or warped_start >= duration - 0.1:
            continue
        warped_duration = warped_end - warped_start
        ws = warped_start
        we = warped_end
        if ws < 0.0:
            ws = 0.0
        we = min(duration, we)
        if we - ws >= 0.30 and warped_duration > 0.0:
            wbpm = max(30.0, min(360.0, 240.0 * num / (den * warped_duration)))
            warped_bars.append((ws, we, wbpm, num, den, warped_start, warped_duration))

    beats: list[BeatEvent] = []
    regions: list[TempoRegion] = []
    for ws, we, wbpm, num, den, warped_start, warped_duration in warped_bars:
        beats.append(BeatEvent(time_sec=round(ws, 4), is_downbeat=True))
        n_beats = 2 if (num == 6 and den == 8) else num
        step = warped_duration / max(1, n_beats)
        for b in range(1, n_beats):
            beat_time = warped_start + b * step
            if ws < beat_time < we:
                beats.append(BeatEvent(time_sec=round(beat_time, 4), is_downbeat=False))

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

    from taikolab.ingest.vocal_riff_tracker import ensure_energy_curve

    ensure_energy_curve(analysis)

    out_json = song_dir / "analysis.json"
    out_json.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    print(f"[TaikoLab TabSync] Saved DTW-aligned analysis -> {out_json} ({len(warped_bars)} measures, {len(regions)} regions)")
    return analysis