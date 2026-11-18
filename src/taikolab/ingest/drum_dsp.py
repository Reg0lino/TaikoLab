"""3-Band waveform transient detector for drums (catches fast double-kick & overlaps)."""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt

from taikolab.ingest.stem_loader import load_mono_audio
from taikolab.schemas import DrumClass, DrumHit


def _bandpass(y: np.ndarray, sr: int, low_hz: float, high_hz: float) -> np.ndarray:
    nyq = 0.5 * sr
    low = max(0.001, low_hz / nyq)
    high = min(0.98, high_hz / nyq)
    if low >= high:
        return y
    sos = butter(4, [low, high], btype="bandpass", output="sos")
    return sosfiltfilt(sos, y).astype(np.float32)


def _detect_band_peaks(
    y_band: np.ndarray,
    sr: int,
    drum_class: DrumClass,
    min_gap_sec: float = 0.055,
    rel_prominence: float = 0.18,
) -> list[DrumHit]:
    hop = 256
    rms = librosa.feature.rms(y=y_band, frame_length=1024, hop_length=hop)[0]
    onset_env = librosa.onset.onset_strength(y=y_band, sr=sr, hop_length=hop)

    if len(onset_env) == 0 or float(np.max(onset_env)) <= 1e-6:
        return []

    env_norm = onset_env / (float(np.percentile(onset_env, 99.5)) + 1e-6)
    rms_norm = rms / (float(np.percentile(rms, 99.5)) + 1e-6)
    min_dist = max(1, int(round(min_gap_sec * sr / hop)))

    peaks, props = find_peaks(
        env_norm,
        height=rel_prominence,
        prominence=rel_prominence * 0.75,
        distance=min_dist,
    )

    hits: list[DrumHit] = []
    for idx, p in enumerate(peaks):
        t = float(librosa.frames_to_time(p, sr=sr, hop_length=hop))
        vel = float(np.clip(0.5 * props["peak_heights"][idx] + 0.5 * rms_norm[min(p, len(rms_norm) - 1)], 0.15, 1.0))
        hits.append(DrumHit(time_sec=round(t, 4), drum_class=drum_class, velocity=round(vel, 3), source="dsp"))
    return hits


def extract_dsp_drum_hits(drums_path: Path) -> list[DrumHit]:
    """Detect Kick (low), Snare (mid), and Crash/Hi-hat (high) peaks on isolated drums."""
    y, sr = load_mono_audio(drums_path, target_sr=44100)
    if len(y) < sr * 0.25:
        return []

    low_y = _bandpass(y, sr, 35.0, 140.0)
    mid_y = _bandpass(y, sr, 200.0, 2400.0)
    high_y = _bandpass(y, sr, 5000.0, 16000.0)

    kicks = _detect_band_peaks(low_y, sr, "kick", min_gap_sec=0.055, rel_prominence=0.22)
    snares = _detect_band_peaks(mid_y, sr, "snare", min_gap_sec=0.065, rel_prominence=0.24)
    crashes = _detect_band_peaks(high_y, sr, "crash", min_gap_sec=0.14, rel_prominence=0.35)

    all_hits = kicks + snares + crashes
    all_hits.sort(key=lambda h: h.time_sec)
    return all_hits