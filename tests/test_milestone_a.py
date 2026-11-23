"""Milestone A tests: GTX-1080 single-segment Demucs pacing, HPSS, solo-drum & 4-bar repair."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
import torch

from taikolab.ingest.stem_loader import separate_stems_demucs, temporary_hpss_percussive_wav
from taikolab.ingest.tab_sync import _coarse_snap_low_harmony_drum_windows, _repair_4bar_drum_drift
from taikolab.schemas import DrumHit


def test_temporary_hpss_percussive_wav_stays_outside_song_dir(tmp_path: Path) -> None:
    sr = 22050
    t = np.linspace(0.0, 1.0, sr, endpoint=False, dtype=np.float32)
    y = 0.3 * np.sin(2.0 * np.pi * 220.0 * t)
    y[4000:4200] += 0.7
    audio = tmp_path / "audio.wav"
    sf.write(str(audio), y, sr)

    with temporary_hpss_percussive_wav(audio) as perc_wav:
        assert perc_wav.exists()
        assert perc_wav.parent != tmp_path
        data, out_sr = sf.read(str(perc_wav), dtype="float32")
        assert out_sr == 44100
        assert len(data) > 0
    assert not perc_wav.exists()


def test_paced_demucs_runs_single_segment_split_false_with_zero_shifts(tmp_path: Path) -> None:
    sr = 44100
    dur_sec = 14.0
    audio = tmp_path / "audio.wav"
    sf.write(str(audio), np.zeros((int(sr * dur_sec), 2), dtype=np.float32), sr)

    class FakeModel:
        samplerate = 44100
        sources = ["drums", "bass", "other", "vocals"]

        def float(self):
            return self

        def to(self, _dev):
            return self

        def eval(self):
            return self

    calls: list[dict] = []

    def fake_apply_model(_model, wav_tensor, **kwargs):
        calls.append({"samples": wav_tensor.shape[-1], **kwargs})
        n_src = 4
        return torch.zeros((1, n_src, 2, wav_tensor.shape[-1]), dtype=torch.float32)

    with (
        patch("demucs.pretrained.get_model", return_value=FakeModel()),
        patch("demucs.apply.apply_model", side_effect=fake_apply_model),
    ):
        stems = separate_stems_demucs(tmp_path, audio, device="cpu", model_name="htdemucs")

    assert Path(stems.drums).exists()
    assert len(calls) >= 2
    for call in calls:
        assert call["split"] is False
        assert call["shifts"] == 0
        assert call["samples"] <= int(6.6 * sr)


def test_coarse_snap_rescues_260ms_solo_drum_intro_and_4bar_repair_fixes_pocket() -> None:
    # Solo drum intro 0..16s (no pitched notes) + band 16..32s
    midi_drums = [
        DrumHit(time_sec=round(t, 4), drum_class="kick" if i % 2 == 0 else "snare", velocity=0.9, source="tab")
        for i, t in enumerate(np.arange(1.0, 31.0, 0.5))
    ]
    # Real audio has solo drum intro shifted +0.26s early (Chroma DTW was 0 lag)
    real_drums = []
    for h in midi_drums:
        shift = 0.26 if h.time_sec < 16.0 else 0.04
        real_drums.append(DrumHit(time_sec=round(h.time_sec + shift, 4), drum_class=h.drum_class, velocity=0.9, source="adtof"))

    knots_m = np.arange(0.0, 34.0, 1.0, dtype=np.float64)
    knots_a = knots_m.copy()
    pitched = np.arange(16.0, 32.0, 0.5, dtype=np.float64)

    knots_m, knots_a, rescued = _coarse_snap_low_harmony_drum_windows(
        knots_m, knots_a, midi_drums, real_drums, pitched, 1.0, 31.0
    )
    assert rescued >= 1

    raw_bars = [(float(b * 2.0), float((b + 1) * 2.0), 120.0, 4, 4) for b in range(15)]
    knots_m, knots_a, repaired = _repair_4bar_drum_drift(
        knots_m, knots_a, raw_bars, midi_drums, real_drums, 32.0
    )

    # Intro drum hits at t=4s..12s should now warp within ~20ms of real_drums (+0.26s)
    warped_intro = np.interp(np.array([4.0, 6.0, 8.0, 10.0]), knots_m, knots_a)
    errs = np.abs(warped_intro - np.array([4.26, 6.26, 8.26, 10.26]))
    assert float(np.max(errs)) <= 0.030

def test_broken_minus2s_dtw_intro_is_rescued_by_healthy_drum_anchors() -> None:
    from taikolab.ingest.tab_sync import _repair_broken_dtw_regions_with_drums

    midi_drums = [
        DrumHit(time_sec=round(t, 4), drum_class="kick" if i % 2 == 0 else "snare", velocity=0.9, source="tab")
        for i, t in enumerate(np.arange(14.0, 60.0, 0.45))
    ]
    # True audio is +0.64s in intro (14..26s) and +0.50s in body (26..60s)
    real_drums = [
        DrumHit(
            time_sec=round(h.time_sec + (0.64 if h.time_sec < 26.0 else 0.50), 4),
            drum_class=h.drum_class,
            velocity=0.9,
            source="adtof",
        )
        for h in midi_drums
    ]
    knots_m = np.arange(0.0, 62.0, 1.0, dtype=np.float64)
    # Simulate broken SyncToolbox DTW pinning 0..24s -2.0s early while body 24..60s is +0.50s
    knots_a = np.array(
        [t - 2.0 if t < 24.0 else t + 0.50 for t in knots_m],
        dtype=np.float64,
    )

    knots_m, knots_a, repaired = _repair_broken_dtw_regions_with_drums(
        knots_m, knots_a, midi_drums, real_drums, 2.9, 60.0
    )
    assert repaired >= 1
    warped_intro = np.interp(np.array([3.0, 15.0, 18.0, 21.0]), knots_m, knots_a)
    errs = np.abs(warped_intro - np.array([3.64, 15.64, 18.64, 21.64]))
    assert float(np.max(errs)) <= 0.040