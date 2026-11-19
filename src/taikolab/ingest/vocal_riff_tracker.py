"""Vocal syllable cadence & guitar/bass riff onset extraction."""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
from scipy.signal import find_peaks

from taikolab.ingest.stem_loader import load_mono_audio
from taikolab.schemas import EnergyPoint, OnsetEvent, SongAnalysis, StemPaths


def extract_vocal_events(vocals_path: Path) -> list[OnsetEvent]:
    """Extract vocal phrase/syllable attacks from isolated vocals.wav."""
    y, sr = load_mono_audio(vocals_path, target_sr=22050)
    if len(y) < sr * 0.25 or float(np.max(np.abs(y))) < 1e-4:
        return []

    hop = 256
    # Focus on vocal fundamental + presence band (150 Hz - 4500 Hz)
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    mask = (freqs >= 150.0) & (freqs <= 4500.0)
    onset_env = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S[mask] + 1e-6), sr=sr, hop_length=hop)
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0]

    peak_env = float(np.percentile(onset_env, 99.0)) + 1e-6
    peak_rms = float(np.percentile(rms, 99.0)) + 1e-6
    env_norm = onset_env / peak_env
    rms_norm = rms / peak_rms

    min_dist = max(1, int(round(0.11 * sr / hop)))  # max ~9 syllables/sec
    peaks, props = find_peaks(env_norm, height=0.20, prominence=0.14, distance=min_dist)

    events: list[OnsetEvent] = []
    for i, p in enumerate(peaks):
        r_val = float(rms_norm[min(p, len(rms_norm) - 1)])
        if r_val < 0.08:
            continue
        t = float(librosa.frames_to_time(p, sr=sr, hop_length=hop))
        strength = float(np.clip(0.6 * props["peak_heights"][i] + 0.4 * r_val, 0.1, 1.0))
        events.append(OnsetEvent(time_sec=round(t, 4), strength=round(strength, 3)))
    return events


def extract_guitar_events(instruments_path: Path) -> list[OnsetEvent]:
    """Extract strong guitar chugs & chord slams from instruments/other stem."""
    y, sr = load_mono_audio(instruments_path, target_sr=22050)
    if len(y) < sr * 0.25 or float(np.max(np.abs(y))) < 1e-4:
        return []

    hop = 256
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0]

    env_norm = onset_env / (float(np.percentile(onset_env, 99.5)) + 1e-6)
    rms_norm = rms / (float(np.percentile(rms, 99.5)) + 1e-6)

    min_dist = max(1, int(round(0.085 * sr / hop)))
    peaks, props = find_peaks(env_norm, height=0.25, prominence=0.18, distance=min_dist)

    events: list[OnsetEvent] = []
    for i, p in enumerate(peaks):
        r_val = float(rms_norm[min(p, len(rms_norm) - 1)])
        t = float(librosa.frames_to_time(p, sr=sr, hop_length=hop))
        strength = float(np.clip(0.55 * props["peak_heights"][i] + 0.45 * r_val, 0.1, 1.0))
        events.append(OnsetEvent(time_sec=round(t, 4), strength=round(strength, 3)))
    return events


def compute_energy_curve(stems: StemPaths, duration_sec: float, step_sec: float = 0.2) -> list[EnergyPoint]:
    """Compute normalized 5Hz stem RMS for arrangement, UI, and diagnostics."""
    def _sampled_rms(path_str: str | None) -> np.ndarray:
        n_bins = max(1, int(np.ceil(duration_sec / step_sec)))
        if not path_str:
            return np.zeros(n_bins, dtype=np.float32)
        y, sr = load_mono_audio(Path(path_str), target_sr=11025)
        hop = max(1, int(round(step_sec * sr)))
        rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
        norm = rms / (float(np.max(rms)) + 1e-6)
        out = np.zeros(n_bins, dtype=np.float32)
        copy_n = min(n_bins, len(norm))
        out[:copy_n] = norm[:copy_n]
        return out

    d_rms = _sampled_rms(stems.drums)
    v_rms = _sampled_rms(stems.vocals)
    g_rms = _sampled_rms(stems.instruments or stems.bass)

    n = max(len(d_rms), len(v_rms), len(g_rms))
    return [
        EnergyPoint(
            time_sec=round(i * step_sec, 2),
            drums_rms=round(float(d_rms[i]), 3),
            vocals_rms=round(float(v_rms[i]), 3),
            guitar_rms=round(float(g_rms[i]), 3),
        )
        for i in range(n)
    ]


def ensure_energy_curve(analysis: SongAnalysis) -> bool:
    """Populate missing cached stem-energy data when at least one stem is available."""
    if analysis.energy_curve:
        return False

    available_stems = StemPaths(
        drums=analysis.stems.drums if analysis.stems.drums and Path(analysis.stems.drums).is_file() else None,
        vocals=analysis.stems.vocals if analysis.stems.vocals and Path(analysis.stems.vocals).is_file() else None,
        bass=analysis.stems.bass if analysis.stems.bass and Path(analysis.stems.bass).is_file() else None,
        instruments=(
            analysis.stems.instruments
            if analysis.stems.instruments and Path(analysis.stems.instruments).is_file()
            else None
        ),
    )
    if not any((available_stems.drums, available_stems.vocals, available_stems.bass, available_stems.instruments)):
        return False

    analysis.energy_curve = compute_energy_curve(available_stems, analysis.duration_sec)
    return True