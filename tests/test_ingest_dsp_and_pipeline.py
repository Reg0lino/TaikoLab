from pathlib import Path

import numpy as np
import soundfile as sf

from taikolab.ingest.drum_dsp import extract_dsp_drum_hits
from taikolab.ingest.stem_loader import discover_song_files
from taikolab.ingest.vocal_riff_tracker import extract_guitar_events, extract_vocal_events


def _write_test_wav(path: Path, samples: np.ndarray, sr: int = 44100) -> None:
    sf.write(str(path), samples.astype(np.float32), sr, subtype="PCM_16")


def test_discover_fl_studio_stems_and_builds_mix(tmp_path: Path):
    sr = 44100
    t = np.linspace(0, 1.0, sr, endpoint=False)
    _write_test_wav(tmp_path / "B.Y.O.B. - Drums.wav", 0.3 * np.sin(2 * np.pi * 60 * t), sr)
    _write_test_wav(tmp_path / "B.Y.O.B. - Vocals.wav", 0.2 * np.sin(2 * np.pi * 440 * t), sr)
    _write_test_wav(tmp_path / "B.Y.O.B. - Instruments.wav", 0.2 * np.sin(2 * np.pi * 220 * t), sr)

    main_audio, stems = discover_song_files(tmp_path)
    assert stems.drums is not None and "Drums" in stems.drums
    assert stems.vocals is not None and "Vocals" in stems.vocals
    assert stems.instruments is not None and "Instruments" in stems.instruments
    assert main_audio.name == "audio_mix.wav"
    assert main_audio.exists()


def test_discover_demucs_stems_keeps_mp3_as_main_audio(tmp_path: Path):
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    main_audio = tmp_path / "theleavingsongpart2.mp3"
    main_audio.touch()
    for stem_name in ("drums", "vocals", "bass", "instruments"):
        (stems_dir / f"{stem_name}.wav").touch()

    discovered_audio, stems = discover_song_files(tmp_path)

    assert discovered_audio == main_audio
    assert Path(stems.drums) == stems_dir / "drums.wav"
    assert Path(stems.vocals) == stems_dir / "vocals.wav"
    assert Path(stems.bass) == stems_dir / "bass.wav"
    assert Path(stems.instruments) == stems_dir / "instruments.wav"


def test_dsp_3band_separates_kick_and_crash(tmp_path: Path):
    sr = 44100
    y = np.zeros(sr * 2, dtype=np.float32)

    # 60 Hz kick burst at t=0.5s
    k_t = np.linspace(0, 0.12, int(sr * 0.12), endpoint=False)
    kick = 0.9 * np.sin(2 * np.pi * 60 * k_t) * np.exp(-k_t * 25)
    y[int(0.5 * sr) : int(0.5 * sr) + len(kick)] += kick

    # High-frequency crash noise burst at t=1.25s
    rng = np.random.default_rng(42)
    c_t = np.linspace(0, 0.25, int(sr * 0.25), endpoint=False)
    raw_noise = rng.standard_normal(len(c_t)).astype(np.float32)
    # Differentiate noise twice to strongly bias high frequencies (>5kHz)
    hi_noise = np.diff(raw_noise, n=2, prepend=[0.0, 0.0])
    hi_noise = hi_noise / (np.max(np.abs(hi_noise)) + 1e-6) * 0.8 * np.exp(-c_t * 10)
    y[int(1.25 * sr) : int(1.25 * sr) + len(hi_noise)] += hi_noise

    wav_path = tmp_path / "drums.wav"
    _write_test_wav(wav_path, y, sr)

    hits = extract_dsp_drum_hits(wav_path)
    kicks = [h for h in hits if h.drum_class == "kick"]
    crashes = [h for h in hits if h.drum_class == "crash"]

    assert any(abs(h.time_sec - 0.5) < 0.05 for h in kicks), f"Missed 60Hz kick: {hits}"
    assert any(abs(h.time_sec - 1.25) < 0.06 for h in crashes), f"Missed crash hit: {hits}"


def test_vocal_and_guitar_onset_extractors(tmp_path: Path):
    sr = 22050
    y = np.zeros(sr * 2, dtype=np.float32)
    for t0 in (0.4, 0.9, 1.4):
        idx = int(t0 * sr)
        seg_t = np.linspace(0, 0.15, int(0.15 * sr), endpoint=False)
        y[idx : idx + len(seg_t)] += 0.8 * np.sin(2 * np.pi * 440 * seg_t) * np.exp(-seg_t * 15)

    v_path = tmp_path / "vocals.wav"
    g_path = tmp_path / "instruments.wav"
    _write_test_wav(v_path, y, sr)
    _write_test_wav(g_path, y, sr)

    v_events = extract_vocal_events(v_path)
    g_events = extract_guitar_events(g_path)
    assert len(v_events) >= 2
    assert len(g_events) >= 2


def test_ml_adapters_import_without_breaking_torch():
    import torch
    import beat_this.inference
    import adtof_pytorch

    assert torch.cuda.is_available()
    assert hasattr(beat_this.inference, "File2Beats")
    assert hasattr(adtof_pytorch, "transcribe_to_midi")