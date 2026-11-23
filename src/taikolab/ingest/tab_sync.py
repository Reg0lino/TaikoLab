"""Pure-mido MIDI Ingest with Band-Wide SyncToolbox Chroma+Onset DTW, Solo-Drum Rescue & 4-Bar Repair."""

from __future__ import annotations

try:
    import torch  # noqa: F401  # Load before numpy/soundfile on Windows to avoid c10.dll init clashes
except Exception:
    pass

from pathlib import Path
from typing import Literal

import mido
import numpy as np
import soundfile as sf

from taikolab.ingest.stem_loader import (
    configure_runtime_threads,
    discover_song_files,
    get_audio_duration,
    temporary_anchor_drums_wav,
    temporary_hpss_percussive_wav,
)
from taikolab.schemas import BeatEvent, DrumClass, DrumHit, OnsetEvent, SongAnalysis, TempoRegion

IngestMode = Literal["cpu_hpss", "gpu_anchors", "gpu_full"]

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


def _find_ffmpeg_exe() -> str | None:
    import shutil

    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _strip_to_first_mp3_frame(data: bytes) -> bytes | None:
    offset = 0
    if data.startswith(b"ID3") and len(data) >= 10:
        tag_size = (
            ((data[6] & 0x7F) << 21)
            | ((data[7] & 0x7F) << 14)
            | ((data[8] & 0x7F) << 7)
            | (data[9] & 0x7F)
        )
        offset = min(len(data), 10 + tag_size)
    limit = min(len(data) - 2, offset + 262144)
    for idx in range(offset, max(offset, limit)):
        if data[idx] == 0xFF and (data[idx + 1] & 0xE0) == 0xE0:
            return data[idx:]
    return data[offset:] if offset > 0 and offset < len(data) else None


def _ensure_clean_simulator_audio(song_dir: Path, main_audio: Path) -> Path:
    """Decode VBR/corrupt-Xing MP3/FLAC once into clean PCM audio_clean.wav for exact sync."""
    if main_audio.suffix.lower() in (".wav", ".ogg"):
        return main_audio

    clean_wav = song_dir / "audio_clean.wav"
    if clean_wav.exists() and clean_wav.stat().st_size > 10000:
        return clean_wav

    import subprocess
    import tempfile
    import librosa

    print(f"[TaikoLab TabSync] Decoding {main_audio.name} -> audio_clean.wav (fixes MP3 seek drift)...")
    try:
        y, sr = librosa.load(str(main_audio), sr=44100, mono=False)
        if y.ndim == 2:
            y = y.T
        sf.write(str(clean_wav), y, sr, subtype="PCM_16")
        return clean_wav
    except Exception as primary_exc:
        ffmpeg_exe = _find_ffmpeg_exe()
        if ffmpeg_exe:
            res = subprocess.run(
                [ffmpeg_exe, "-y", "-i", str(main_audio), "-vn", "-acodec", "pcm_s16le", "-ar", "44100", str(clean_wav)],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0 and clean_wav.exists() and clean_wav.stat().st_size > 10000:
                return clean_wav

        if main_audio.suffix.lower() == ".mp3":
            raw = main_audio.read_bytes()
            if len(raw) >= 12 and raw[4:8] == b"ftyp":
                raise RuntimeError(
                    f"{main_audio.name} is actually an MP4/M4A container renamed to .mp3, and ffmpeg is not installed. "
                    "Convert it to WAV/FLAC or install ffmpeg."
                ) from primary_exc
            trimmed = _strip_to_first_mp3_frame(raw)
            if trimmed:
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                    tmp_path = Path(tmp.name)
                try:
                    tmp_path.write_bytes(trimmed)
                    y, sr = librosa.load(str(tmp_path), sr=44100, mono=False)
                    if y.ndim == 2:
                        y = y.T
                    sf.write(str(clean_wav), y, sr, subtype="PCM_16")
                    return clean_wav
                except Exception:
                    pass
                finally:
                    tmp_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Could not decode {main_audio.name}. If it has corrupt frames or is not a standard MP3/FLAC, "
            "convert it to WAV/FLAC or install ffmpeg."
        ) from primary_exc


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
            drums.append(DrumHit(time_sec=round(s_sec, 4), drum_class=d_cls, velocity=round(vel, 3), source="tab", pitch=int(pitch)))
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
        for lag in np.arange(initial_lag - 1.00, initial_lag + 1.0001, 0.02):
            consider(float(rate), float(lag))
    coarse_mid_lag = best_lag
    coarse_mid_rate = best_rate
    for rate in np.arange(max(0.90, coarse_mid_rate - 0.004), min(1.10, coarse_mid_rate + 0.0041), 0.001):
        for lag in np.arange(coarse_mid_lag - 0.04, coarse_mid_lag + 0.0401, 0.005):
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

    ks_times = sorted(h.time_sec for h in midi_drums if h.drum_class in ("kick", "snare"))
    anchor_midi_sec = ks_times[len(ks_times) // 2]
    anchor_audio_sec = float(np.interp(anchor_midi_sec, dtw_knots_m, dtw_knots_a))
    affine_m, affine_a, affine_rate = _estimate_classified_drum_warp(
        midi_drums,
        real_drums,
        duration_sec=duration_sec,
        anchor_midi_sec=anchor_midi_sec,
        anchor_audio_sec=anchor_audio_sec,
    )
    if abs(affine_rate - 1.0) <= 0.0005:
        lag = float(np.median(affine_a - affine_m))
        const_a = affine_m + lag
        const_matches, _ = _score_drum_warp(midi_drums, real_drums, affine_m, const_a, tolerance_sec=0.020)
        if const_matches >= int(0.96 * total_hits):
            affine_a = const_a
            affine_rate = 1.0

    affine_matches, _ = _score_drum_warp(midi_drums, real_drums, affine_m, affine_a)
    required_improvement = max(12, int(np.ceil(total_hits * 0.05)))
    if affine_matches >= current_matches + required_improvement:
        return affine_m, affine_a, affine_matches, total_hits, affine_rate
    return dtw_knots_m, dtw_knots_a, current_matches, total_hits, None


def _clamp_monotonic_knots(knots_m: np.ndarray, raw_a: np.ndarray, min_slope: float = 0.82, max_slope: float = 1.22) -> np.ndarray:
    if len(knots_m) <= 1:
        return raw_a.copy()
    clamped = [float(raw_a[0])]
    for i in range(1, len(knots_m)):
        dm = float(knots_m[i] - knots_m[i - 1])
        da = float(raw_a[i] - clamped[-1])
        da = max(dm * min_slope, min(dm * max_slope, da))
        clamped.append(clamped[-1] + da)
    return np.array(clamped, dtype=np.float64)


def _repair_broken_dtw_regions_with_drums(
    knots_m: np.ndarray,
    knots_a: np.ndarray,
    midi_drums: list[DrumHit],
    real_drums: list[DrumHit],
    first_note_t: float,
    last_note_t: float,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Replace broken DTW regions (e.g. -2s intro drift) using healthy-chunk drum lags."""
    m_by_cls = {
        cls: np.array(sorted(h.time_sec for h in midi_drums if h.drum_class == cls), dtype=np.float64)
        for cls in ("kick", "snare", "tom")
    }
    r_by_cls = {
        cls: np.array(sorted(h.time_sec for h in real_drums if h.drum_class == cls), dtype=np.float64)
        for cls in ("kick", "snare", "tom")
    }
    m_ks = np.sort(np.concatenate([m_by_cls["kick"], m_by_cls["snare"]]))
    r_ks = np.sort(np.concatenate([r_by_cls["kick"], r_by_cls["snare"]]))
    if len(m_ks) < 12 or len(r_ks) < 12:
        return knots_m, knots_a, 0

    def _window_pairs(w0: float, w1: float, use_raw: bool) -> list[tuple[np.ndarray, np.ndarray]]:
        out_p: list[tuple[np.ndarray, np.ndarray]] = []
        for cls in ("kick", "snare", "tom"):
            mc = m_by_cls[cls]
            mw = mc[(mc >= w0) & (mc < w1)]
            rc = r_by_cls[cls]
            if len(mw) > 0 and len(rc) > 0:
                base = mw if use_raw else np.interp(mw, knots_m, knots_a)
                out_p.append((base, rc))
        return out_p

    def _score_shift(pairs: list[tuple[np.ndarray, np.ndarray]], shift_s: float, tol: float = 0.035) -> int:
        tot = 0
        for base, rc in pairs:
            sh = base + shift_s
            idx = np.searchsorted(rc, sh)
            d1 = np.abs(rc[np.minimum(idx, len(rc) - 1)] - sh)
            d0 = np.abs(rc[np.maximum(idx - 1, 0)] - sh)
            tot += int(np.sum(np.minimum(d1, d0) <= tol))
        return tot

    # 1. Global fallback lag across [-4.0s, +4.0s]
    global_pairs = [(m_by_cls["kick"], r_by_cls["kick"]), (m_by_cls["snare"], r_by_cls["snare"])]
    global_pairs = [(a, b) for a, b in global_pairs if len(a) > 0 and len(b) > 0]
    global_lag, global_best = 0.0, -1
    if global_pairs:
        for lag in np.arange(-4.0, 4.01, 0.025):
            sc = _score_shift(global_pairs, float(lag), tol=0.040)
            if sc > global_best or (sc == global_best and abs(lag) < abs(global_lag)):
                global_best, global_lag = sc, float(lag)

    # 2. Sample healthy 12s windows from t=0.0 onward
    win_size = 12.0
    healthy_centers: list[float] = []
    healthy_lags: list[float] = []
    windows = list(np.arange(0.0, last_note_t, win_size))
    dtw_stats: list[tuple[float, float, float, float, int]] = []

    for w0 in windows:
        w1 = w0 + win_size
        pairs_dtw = _window_pairs(w0, w1, use_raw=False)
        n_hits = sum(len(b) for b, _ in pairs_dtw)
        k_sub = knots_a[(knots_m >= w0) & (knots_m < w1)] - knots_m[(knots_m >= w0) & (knots_m < w1)]
        cur_lag = float(np.median(k_sub)) if len(k_sub) else global_lag
        if n_hits < 5:
            dtw_stats.append((w0, w1, 1.0, cur_lag, n_hits))
            continue
        cur_m = _score_shift(pairs_dtw, 0.0, tol=0.038)
        cur_rate = cur_m / float(n_hits)
        dtw_stats.append((w0, w1, cur_rate, cur_lag, n_hits))
        if cur_rate >= 0.58 and n_hits >= 8:
            healthy_centers.append(0.5 * (w0 + w1))
            healthy_lags.append(cur_lag)

    if len(healthy_lags) >= 2:
        med_hl = float(np.median(healthy_lags))
        filtered = [(c, l) for c, l in zip(healthy_centers, healthy_lags) if abs(l - med_hl) <= 0.65]
        if filtered:
            healthy_centers = [c for c, _ in filtered]
            healthy_lags = [l for _, l in filtered]

    repaired_a = knots_a.copy()
    repaired = 0
    valid_centers: list[float] = []
    valid_lags: list[float] = []
    drumless_windows: list[tuple[float, float, float]] = []

    for w0, w1, cur_rate, cur_lag, n_hits in dtw_stats:
        center = 0.5 * (w0 + w1)
        if n_hits < 5:
            drumless_windows.append((w0, w1, cur_lag))
            continue

        ref_lag = float(np.interp(center, healthy_centers, healthy_lags)) if healthy_centers else global_lag
        if cur_rate >= 0.50 and abs(cur_lag - ref_lag) <= 0.30:
            valid_centers.append(center)
            valid_lags.append(cur_lag)
            continue

        pairs_raw = _window_pairs(w0, w1, use_raw=True)
        cur_matches = _score_shift(_window_pairs(w0, w1, use_raw=False), 0.0, tol=0.038)
        best_lag = ref_lag
        best_c = -1
        for cand_lag in np.arange(ref_lag - 1.25, ref_lag + 1.255, 0.015):
            c = _score_shift(pairs_raw, float(cand_lag), tol=0.035)
            if c > best_c or (c == best_c and abs(cand_lag - ref_lag) < abs(best_lag - ref_lag)):
                best_c, best_lag = c, float(cand_lag)

        if best_c >= max(4, int(np.ceil(0.45 * n_hits))) and best_c >= cur_matches + 2:
            k_mask = (knots_m >= w0) & (knots_m < w1)
            repaired_a[k_mask] = knots_m[k_mask] + best_lag
            repaired += 1
            valid_centers.append(center)
            valid_lags.append(best_lag)
        else:
            valid_centers.append(center)
            valid_lags.append(cur_lag)

    # Back-fill / forward-fill silent or hi-hat-only windows if DTW wandered >0.30s from nearest valid drum window
    if repaired and valid_centers:
        for w0, w1, cur_lag in drumless_windows:
            nearest_lag = float(np.interp(0.5 * (w0 + w1), valid_centers, valid_lags))
            if abs(cur_lag - nearest_lag) > 0.30:
                k_mask = (knots_m >= w0) & (knots_m < w1)
                repaired_a[k_mask] = knots_m[k_mask] + nearest_lag

    if repaired:
        lags = repaired_a - knots_m
        kernel = np.array([0.20, 0.60, 0.20], dtype=np.float64)
        smooth_lags = np.convolve(np.pad(lags, (1, 1), mode="edge"), kernel, mode="valid")
        repaired_a = _clamp_monotonic_knots(knots_m, knots_m + smooth_lags, min_slope=0.82, max_slope=1.22)

    return knots_m, repaired_a, repaired


def _coarse_snap_low_harmony_drum_windows(
    knots_m: np.ndarray,
    knots_a: np.ndarray,
    midi_drums: list[DrumHit],
    real_drums: list[DrumHit],
    pitched_times: np.ndarray,
    first_note_t: float,
    last_note_t: float,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Rescue pure-drum intros/breaks where Chroma DTW lacked harmonic anchor (+/-400ms search)."""
    m_by_cls = {
        cls: np.array(sorted(h.time_sec for h in midi_drums if h.drum_class == cls), dtype=np.float64)
        for cls in ("kick", "snare", "tom")
    }
    r_by_cls = {
        cls: np.array(sorted(h.time_sec for h in real_drums if h.drum_class == cls), dtype=np.float64)
        for cls in ("kick", "snare", "tom")
    }
    m_core = np.sort(np.concatenate([m_by_cls[c] for c in ("kick", "snare", "tom")]))
    r_core = np.sort(np.concatenate([r_by_cls[c] for c in ("kick", "snare", "tom")]))
    if len(m_core) < 6 or len(r_core) < 6:
        return knots_m, knots_a, 0

    coarse_grid = np.arange(-0.40, 0.405, 0.010)
    refined_a = knots_a.copy()
    rescued = 0
    win_step = 6.0

    for win_start in np.arange(first_note_t, last_note_t, win_step):
        win_end = win_start + win_step
        eval_end = win_start + 7.5
        m_win_all = m_core[(m_core >= win_start) & (m_core < eval_end)]
        if len(m_win_all) < 5:
            continue

        pairs: list[tuple[np.ndarray, np.ndarray]] = []
        for cls in ("kick", "snare", "tom"):
            mc = m_by_cls[cls]
            mw = mc[(mc >= win_start) & (mc < eval_end)]
            rc = r_by_cls[cls]
            if len(mw) > 0 and len(rc) > 0:
                pairs.append((np.interp(mw, knots_m, knots_a), rc))
        if not pairs:
            pairs = [(np.interp(m_win_all, knots_m, knots_a), r_core)]

        total_win = sum(len(bw) for bw, _ in pairs)
        if total_win < 5:
            continue

        def _count_matches(shift_sec: float) -> int:
            tot = 0
            for bw, rc in pairs:
                sh = bw + shift_sec
                idx = np.searchsorted(rc, sh)
                d1 = np.abs(rc[np.minimum(idx, len(rc) - 1)] - sh)
                d0 = np.abs(rc[np.maximum(idx - 1, 0)] - sh)
                tot += int(np.sum(np.minimum(d1, d0) <= 0.032))
            return tot

        n_pitched = int(np.sum((pitched_times >= win_start) & (pitched_times < eval_end)))
        base_matches = _count_matches(0.0)
        base_rate = base_matches / float(total_win)

        is_pure_drum = n_pitched <= 2
        if not (is_pure_drum or base_rate < 0.45):
            continue

        best_shift = 0.0
        best_c = base_matches
        for ng in coarse_grid:
            c = _count_matches(float(ng))
            if c > best_c or (c == best_c and abs(ng) < abs(best_shift)):
                best_c, best_shift = c, float(ng)

        if abs(best_shift) >= 0.035 and best_c >= max(4, int(np.ceil(0.55 * total_win))) and best_c >= base_matches + 3:
            k_mask = (knots_m >= win_start) & (knots_m < win_end)
            refined_a[k_mask] += best_shift
            rescued += 1

    if rescued:
        lags = refined_a - knots_m
        kernel = np.array([0.20, 0.60, 0.20], dtype=np.float64)
        smooth_lags = np.convolve(np.pad(lags, (1, 1), mode="edge"), kernel, mode="valid")
        refined_a = _clamp_monotonic_knots(knots_m, knots_m + smooth_lags)
    return knots_m, refined_a, rescued


def _repair_4bar_drum_drift(
    knots_m: np.ndarray,
    knots_a: np.ndarray,
    raw_bars: list[tuple[float, float, float, int, int]],
    midi_drums: list[DrumHit],
    real_drums: list[DrumHit],
    duration_sec: float,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Repair 4-bar phrases that sit >25ms away from real drum transients."""
    if len(raw_bars) < 4 or len(real_drums) < 8:
        return knots_m, knots_a, 0

    m_ks = np.array(
        sorted(h.time_sec for h in midi_drums if h.drum_class in ("kick", "snare")),
        dtype=np.float64,
    )
    r_ks = np.array(
        sorted(h.time_sec for h in real_drums if h.drum_class in ("kick", "snare")),
        dtype=np.float64,
    )
    if len(m_ks) < 8 or len(r_ks) < 8:
        return knots_m, knots_a, 0

    # Ensure 1.0s knot density so affine warps can also repair local rubato sections smoothly
    if len(knots_m) <= 4:
        dense_m = np.arange(0.0, max(duration_sec, 1.0) + 1.0, 1.0, dtype=np.float64)
        dense_a = np.interp(dense_m, knots_m, knots_a)
    else:
        dense_m = knots_m.copy()
        dense_a = knots_a.copy()

    repaired = 0
    for start_bar in range(0, len(raw_bars), 4):
        end_bar = min(len(raw_bars) - 1, start_bar + 3)
        m_t0 = raw_bars[start_bar][0]
        m_t1 = raw_bars[end_bar][1]
        chunk_m = m_ks[(m_ks >= m_t0) & (m_ks < m_t1)]
        if len(chunk_m) < 4:
            continue

        warped_chunk = np.interp(chunk_m, dense_m, dense_a)
        idx = np.searchsorted(r_ks, warped_chunk)
        right = r_ks[np.minimum(idx, len(r_ks) - 1)] - warped_chunk
        left = r_ks[np.maximum(idx - 1, 0)] - warped_chunk
        signed = np.where(np.abs(left) < np.abs(right), left, right)
        near = signed[np.abs(signed) <= 0.14]
        if len(near) < max(3, len(chunk_m) // 2):
            continue

        med_err = float(np.median(near))
        if abs(med_err) <= 0.025:
            continue

        consistent = int(np.sum(np.abs(near - med_err) <= 0.035))
        if consistent < max(3, int(np.ceil(len(near) * 0.55))):
            continue

        k_mask = (dense_m >= m_t0) & (dense_m <= m_t1)
        if np.any(k_mask):
            dense_a[k_mask] += med_err
            repaired += 1

    if repaired:
        lags = dense_a - dense_m
        kernel = np.array([0.25, 0.50, 0.25], dtype=np.float64)
        smooth_lags = np.convolve(np.pad(lags, (1, 1), mode="edge"), kernel, mode="valid")
        dense_a = _clamp_monotonic_knots(dense_m, dense_m + smooth_lags)

    return dense_m, dense_a, repaired


def _align_via_synctoolbox(
    audio_path: Path,
    synth_guide: list[tuple[float, float, int, bool]],
    midi_drums: list[DrumHit],
    real_drums: list[DrumHit],
    duration_sec: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Run band-wide SyncToolbox Chroma+Onset DTW alignment, solo-drum rescue, and drum snap."""
    import librosa
    from synctoolbox.dtw.mrmsdtw import sync_via_mrmsdtw
    from synctoolbox.dtw.utils import compute_optimal_chroma_shift, shift_chroma_vectors
    from synctoolbox.feature.chroma import pitch_to_chroma, quantize_chroma, quantized_chroma_to_CENS
    from synctoolbox.feature.dlnco import pitch_onset_features_to_DLNCO
    from synctoolbox.feature.pitch import audio_to_pitch_features
    from synctoolbox.feature.pitch_onset import audio_to_pitch_onset_features
    from synctoolbox.feature.utils import estimate_tuning

    configure_runtime_threads()
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

    first_note_t = min((n[0] for n in synth_guide), default=0.0)
    last_note_t = max((n[0] for n in synth_guide), default=duration_sec)

    knots_m = np.arange(0.0, last_note_t + 2.0, 1.0)
    knots_a = np.interp(knots_m, t_mid_wp, t_aud_wp)

    m_ks = np.array(sorted(h.time_sec for h in midi_drums if h.drum_class in ("kick", "snare")), dtype=np.float64)
    r_ks = np.array(sorted(h.time_sec for h in real_drums if h.drum_class in ("kick", "snare")), dtype=np.float64)

    # Project first and last solid drum groove through quiet guitar intro/outro (fixes Verse 1 & Outro drift)
    if len(m_ks) >= 12 and len(r_ks) >= 12:
        m_intro = m_ks[:min(18, len(m_ks))]
        first_drum_t = float(m_intro[0])
        intro_candidates = r_ks[:min(40, len(r_ks))]
        best_intro_lag = float(np.interp(first_drum_t, knots_m, knots_a) - first_drum_t)
        best_sc = -1
        for cand_lag in np.arange(best_intro_lag - 0.45, best_intro_lag + 0.455, 0.005):
            sh = m_intro + cand_lag
            idx = np.searchsorted(intro_candidates, sh)
            d1 = np.abs(intro_candidates[np.minimum(idx, len(intro_candidates) - 1)] - sh)
            d0 = np.abs(intro_candidates[np.maximum(idx - 1, 0)] - sh)
            sc = int(np.sum(np.minimum(d1, d0) <= 0.030))
            if sc > best_sc:
                best_sc, best_intro_lag = sc, float(cand_lag)

        anchor_in_t = max(0.0, first_drum_t)
        for i, tm in enumerate(knots_m):
            if tm <= anchor_in_t:
                knots_a[i] = tm + best_intro_lag

        m_outro = m_ks[max(0, len(m_ks) - 18):]
        last_drum_t = float(m_outro[-1])
        best_outro_lag = float(np.interp(last_drum_t, knots_m, knots_a) - last_drum_t)
        anchor_out_t = min(last_note_t, last_drum_t)
        for i, tm in enumerate(knots_m):
            if tm >= anchor_out_t:
                knots_a[i] = tm + best_outro_lag
    else:
        anchor_in_t = min(last_note_t, first_note_t + 2.5)
        anchor_out_t = max(anchor_in_t + 1.0, last_note_t - 2.0)
        lag_in = float(np.interp(anchor_in_t, t_mid_wp, t_aud_wp) - anchor_in_t)
        lag_out = float(np.interp(anchor_out_t, t_mid_wp, t_aud_wp) - anchor_out_t)
        for i, tm in enumerate(knots_m):
            if tm < anchor_in_t:
                knots_a[i] = tm + lag_in
            elif tm > anchor_out_t:
                knots_a[i] = tm + lag_out

    # Only run gross DTW replacement if initial match rate is under 55% (preserves already-locked songs)
    init_matches, init_total = _score_drum_warp(midi_drums, real_drums, knots_m, knots_a, tolerance_sec=0.038)
    init_pct = (init_matches / float(init_total)) if init_total else 1.0
    if init_pct < 0.55:
        knots_m, knots_a, repaired_dtw_wins = _repair_broken_dtw_regions_with_drums(
            knots_m,
            knots_a,
            midi_drums,
            real_drums,
            first_note_t,
            last_note_t,
        )
        if repaired_dtw_wins:
            print(f"[TaikoLab TabSync] Gross DTW repair rescued {repaired_dtw_wins} region(s) (init match was {init_pct * 100:.1f}%).")

    # Continuous sliding-window drum transient alignment (step 2.0s, window 8.0s)
    if len(m_ks) >= 10 and len(r_ks) >= 10:
        nudge_grid = np.arange(-0.035, 0.036, 0.005)
        raw_lags = knots_a - knots_m
        sample_lags = raw_lags.copy()

        for win_center in np.arange(first_note_t, last_note_t + 1.0, 2.0):
            mask_m = (m_ks >= win_center - 4.0) & (m_ks < win_center + 4.0)
            if int(np.sum(mask_m)) < 4:
                continue
            base_w = np.interp(m_ks[mask_m], knots_m, knots_a)
            best_n, best_c = 0.0, -1
            for ng in nudge_grid:
                sh = base_w + ng
                idx = np.searchsorted(r_ks, sh)
                d1 = np.abs(r_ks[np.minimum(idx, len(r_ks) - 1)] - sh)
                d0 = np.abs(r_ks[np.maximum(idx - 1, 0)] - sh)
                c = int(np.sum(np.minimum(d1, d0) <= 0.025))
                if c > best_c:
                    best_c, best_n = c, float(ng)
            if best_c / float(np.sum(mask_m)) >= 0.55 and abs(best_n) >= 0.005:
                k_idx = int(round(np.clip(win_center, 0, len(knots_m) - 1)))
                sample_lags[k_idx] += best_n

        # 9-point Gaussian kernel (sigma=2.2 knots) produces continuous smooth tempo curves without jagged steps
        g_weights = np.exp(-0.5 * (np.arange(-4, 5, dtype=np.float64) / 2.2) ** 2)
        g_weights /= np.sum(g_weights)
        smooth_lags = np.convolve(np.pad(sample_lags, (4, 4), mode="edge"), g_weights, mode="valid")
        # Restrict max bar-to-bar slope to [0.93, 1.08] (prevents 170-238 spikes while tracking live push/pull)
        knots_a = _clamp_monotonic_knots(knots_m, knots_m + smooth_lags, min_slope=0.93, max_slope=1.08)

        final_warped = np.interp(m_ks, knots_m, knots_a)
        idx = np.searchsorted(r_ks, final_warped)
        d1 = np.abs(r_ks[np.minimum(idx, len(r_ks) - 1)] - final_warped)
        d0 = np.abs(r_ks[np.maximum(idx - 1, 0)] - final_warped)
        match_pct = 100.0 * float(np.mean(np.minimum(d1, d0) <= 0.035))
        print(f"[TaikoLab TabSync] Gaussian-smoothed drum clock locked {match_pct:.1f}% of all Kick/Snare hits within +/-35ms!")

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


def _print_sync_audit(
    warped_bars: list[tuple[float, float, float, int, int, float, float]],
    warped_drums: list[DrumHit],
    real_drums: list[DrumHit],
) -> None:
    """Report any 4-bar phrases whose drum hits deviate >35ms from detected drum transients."""
    midi_ks = np.array(
        sorted(h.time_sec for h in warped_drums if h.drum_class in ("kick", "snare")),
        dtype=np.float64,
    )
    real_ks = np.array(
        sorted(h.time_sec for h in real_drums if h.drum_class in ("kick", "snare")),
        dtype=np.float64,
    )
    if len(midi_ks) < 8 or len(real_ks) < 8:
        return

    flagged: list[str] = []
    for start_bar in range(0, len(warped_bars), 4):
        end_bar = min(len(warped_bars) - 1, start_bar + 3)
        t0 = warped_bars[start_bar][0]
        t1 = warped_bars[end_bar][1]
        chunk = midi_ks[(midi_ks >= t0) & (midi_ks < t1)]
        if len(chunk) < 4:
            continue
        idx = np.searchsorted(real_ks, chunk)
        right = real_ks[np.minimum(idx, len(real_ks) - 1)] - chunk
        left = real_ks[np.maximum(idx - 1, 0)] - chunk
        signed = np.where(np.abs(left) < np.abs(right), left, right)
        near = signed[np.abs(signed) <= 0.12]
        if len(near) < max(3, len(chunk) // 2):
            continue
        med_ms = float(np.median(near) * 1000.0)
        if abs(med_ms) > 35.0:
            flagged.append(f"bars {start_bar}-{end_bar} ({med_ms:+.0f}ms)")

    if flagged:
        shown = ", ".join(flagged[:6])
        more = f" (+{len(flagged) - 6} more)" if len(flagged) > 6 else ""
        print(f"[TaikoLab SyncAudit] Residual offset spots >35ms vs drums: {shown}{more}")
    else:
        print("[TaikoLab SyncAudit] All 4-bar phrases are within +/-35ms of drum transients!")


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

            if len(bar_hits) >= 8 and has_sustained_fast_run:
                mid = 0.5 * (start + end)
                out.append((start, mid, min(360.0, bpm * 2.0), 4, 4))
                out.append((mid, end, min(360.0, bpm * 2.0), 4, 4))
                continue
        out.append((start, end, bpm, num, den))
    return out


def _detect_anchor_focus_times(
    drums: list[DrumHit],
    duration_sec: float,
) -> list[float]:
    core_times = sorted(h.time_sec for h in drums if h.drum_class in ("kick", "snare", "tom"))
    if not core_times:
        return [min(duration_sec * 0.15, 10.0), duration_sec * 0.5, max(0.0, duration_sec - 10.0)]
    first_t = min(duration_sec - 6.0, core_times[0] + 6.0)
    mid_t = core_times[len(core_times) // 2]
    last_t = max(6.0, core_times[-1] - 6.0)
    return [max(0.0, first_t), max(0.0, mid_t), max(0.0, last_t)]


def _transcribe_real_drums_for_alignment(
    main_audio: Path,
    stems,
    midi_drums: list[DrumHit],
    duration_sec: float,
    effective_mode: IngestMode,
    device: str,
    demucs_model: str,
) -> list[DrumHit]:
    from taikolab.ingest.drum_dsp import extract_dsp_drum_hits
    from taikolab.ingest.drum_transcriber import run_adtof_transcription, transcribe_drums_hybrid

    if stems.drums:
        return transcribe_drums_hybrid(Path(stems.drums), has_isolated_drums=True, device="cpu")

    # Always prepare CPU HPSS percussive detections (0 VRAM, removes distorted guitar fuzz)
    with temporary_hpss_percussive_wav(main_audio) as hpss_wav:
        adtof_hpss = run_adtof_transcription(hpss_wav, device="cpu")
        dsp_hpss = [
            h for h in extract_dsp_drum_hits(hpss_wav)
            if h.drum_class in ("kick", "snare", "tom") and h.velocity >= 0.48
        ]
    hpss_hits: list[DrumHit] = [h.model_copy() for h in adtof_hpss]
    for dh in dsp_hpss:
        if not any(ah.drum_class == dh.drum_class and abs(ah.time_sec - dh.time_sec) <= 0.038 for ah in hpss_hits):
            hpss_hits.append(dh)
    hpss_hits.sort(key=lambda h: h.time_sec)

    if effective_mode == "gpu_anchors" and duration_sec >= 15.0:
        try:
            focus_times = _detect_anchor_focus_times(midi_drums, duration_sec)
            with temporary_anchor_drums_wav(
                main_audio,
                focus_times=focus_times,
                device=device,
                model_name=demucs_model,
            ) as (anchor_wav, anchor_intervals):
                anchor_hits = transcribe_drums_hybrid(anchor_wav, has_isolated_drums=True, device="cpu")
            in_anchor = [
                h for h in anchor_hits
                if any(s <= h.time_sec <= e for s, e in anchor_intervals)
            ]
            outside_anchor = [
                h for h in hpss_hits
                if not any(s <= h.time_sec <= e for s, e in anchor_intervals)
            ]
            combined = sorted(in_anchor + outside_anchor, key=lambda h: h.time_sec)
            print(
                f"[TaikoLab TabSync] Anchor drums: {len(in_anchor)} isolated Demucs hits + "
                f"{len(outside_anchor)} HPSS hits."
            )
            return combined
        except Exception as exc:
            print(f"[TaikoLab TabSync] Anchor GPU Demucs skipped ({exc}); using CPU HPSS.")

    print(f"[TaikoLab TabSync] CPU HPSS percussive drum transcription: {len(hpss_hits)} hits (0 MB VRAM).")
    return hpss_hits


def import_midi_aligned(
    song_id: str,
    song_dir: Path,
    midi_path: Path,
    title: str | None = None,
    artist: str = "Unknown",
    manual_offset_sec: float | None = None,
    device: str = "cuda",
    use_stems: bool = False,
    ingest_mode: IngestMode | None = None,
    demucs_model: str = "htdemucs",
    gp_path: Path | None = None,
) -> SongAnalysis:
    """Parse a MIDI tab and align it to real audio via SyncToolbox Chroma+Onset DTW."""
    if not midi_path.exists():
        raise FileNotFoundError(f"MIDI file not found: {midi_path}")

    configure_runtime_threads()
    effective_mode: IngestMode = ingest_mode if ingest_mode is not None else ("gpu_full" if use_stems else "cpu_hpss")

    raw_main_audio, stems = discover_song_files(song_dir)
    main_audio = _ensure_clean_simulator_audio(song_dir, raw_main_audio)
    duration = get_audio_duration(main_audio)

    effective_device = device
    if manual_offset_sec is None and effective_mode == "gpu_full" and not stems.drums and duration >= 15.0:
        try:
            from taikolab.ingest.stem_loader import separate_stems_demucs

            stems = separate_stems_demucs(song_dir, main_audio, device=effective_device, model_name=demucs_model)
        except Exception as exc:
            print(f"[TaikoLab TabSync] Demucs stem separation skipped ({exc}); falling back to CPU HPSS.")
            if "cuda" in str(exc).lower():
                effective_device = "cpu"

    bars, drums, vocals, guitars, synth_guide = _parse_midi_unified(midi_path)
    print(
        f"[TaikoLab TabSync] Parsed MIDI (pure mido): {len(bars)} bars | "
        f"{len(drums)} drums | {len(vocals)} vocals | {len(guitars)} guitar strokes"
    )
    raw_midi_bars = list(bars)
    bars = _split_double_time_bars(bars, drums)

    real_drums: list[DrumHit] = []
    if manual_offset_sec is not None:
        knots_m = np.array([0.0, duration], dtype=np.float64)
        knots_a = knots_m + manual_offset_sec
        print(f"[TaikoLab TabSync] Manual offset override: {manual_offset_sec:+.3f}s")
    else:
        real_drums = _transcribe_real_drums_for_alignment(
            main_audio,
            stems,
            drums,
            duration,
            effective_mode,
            effective_device,
            demucs_model,
        )
        knots_m, knots_a = _align_via_synctoolbox(main_audio, synth_guide, drums, real_drums, duration)
        knots_m, knots_a, repaired_phrases = _repair_4bar_drum_drift(
            knots_m,
            knots_a,
            bars,
            drums,
            real_drums,
            duration,
        )
        if repaired_phrases:
            print(f"[TaikoLab TabSync] Auto-repaired {repaired_phrases} 4-bar phrase(s) >25ms off drum transients.")

    def warp(t: float) -> float:
        if len(knots_m) > 1 and t < knots_m[0]:
            slope = (knots_a[1] - knots_a[0]) / (knots_m[1] - knots_m[0])
            return float(knots_a[0] + (t - knots_m[0]) * slope)
        if len(knots_m) > 1 and t > knots_m[-1]:
            slope = (knots_a[-1] - knots_a[-2]) / (knots_m[-1] - knots_m[-2])
            return float(knots_a[-1] + (t - knots_m[-1]) * slope)
        return float(np.interp(t, knots_m, knots_a))

    warped_drums = [
        DrumHit(time_sec=round(wt, 4), drum_class=h.drum_class, velocity=h.velocity, source="tab", pitch=h.pitch)
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
        ws = max(0.0, warped_start)
        we = min(duration, warped_end)
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

    gp_hints = None
    if gp_path is not None and gp_path.is_file():
        from taikolab.ingest.gp_parser import parse_gp_hints

        gp_hints = parse_gp_hints(gp_path)
        for sec in gp_hints.sections:
            if 0 <= sec.bar_idx < len(raw_midi_bars):
                sec.time_sec = round(max(0.0, warp(raw_midi_bars[sec.bar_idx][0])), 4)
        if gp_hints.bar_meters:
            updated_bars = []
            for b_idx, (b_ws, b_we, b_bpm, b_n, b_d, b_orig_s, b_orig_dur) in enumerate(warped_bars):
                if b_idx in gp_hints.bar_meters:
                    gp_n, gp_d = gp_hints.bar_meters[b_idx]
                    if (gp_n, gp_d) != (b_n, b_d):
                        new_bpm = max(30.0, min(360.0, 240.0 * gp_n / (gp_d * max(0.15, b_orig_dur))))
                        updated_bars.append((b_ws, b_we, new_bpm, gp_n, gp_d, b_orig_s, b_orig_dur))
                        continue
                updated_bars.append((b_ws, b_we, b_bpm, b_n, b_d, b_orig_s, b_orig_dur))
            warped_bars = updated_bars
        print(
            f"[TaikoLab TabSync] Parsed GP sidecar ({gp_path.name}): "
            f"{len(gp_hints.sections)} sections, {len(gp_hints.accent_bars)} accent bars, "
            f"{len(gp_hints.palm_mute_bars)} palm-mute bars"
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
        gp_hints=gp_hints,
    )

    from taikolab.ingest.vocal_riff_tracker import ensure_energy_curve

    ensure_energy_curve(analysis, song_dir=song_dir)

    if manual_offset_sec is None and len(warped_bars) >= 4:
        _print_sync_audit(warped_bars, warped_drums, real_drums)

    out_json = song_dir / "analysis.json"
    out_json.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    print(f"[TaikoLab TabSync] Saved DTW-aligned analysis -> {out_json} ({len(warped_bars)} measures, {len(regions)} regions)")
    return analysis