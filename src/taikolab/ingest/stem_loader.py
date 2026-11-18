"""Audio & FL Studio stem discovery and safe WAV loading."""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from taikolab.schemas import StemPaths

AUDIO_EXTS = (".wav", ".ogg", ".flac", ".mp3")


def discover_song_files(song_dir: Path) -> tuple[Path, StemPaths]:
    """Locate main mix audio and FL Studio / Demucs stems inside a song workspace."""
    if not song_dir.exists():
        raise FileNotFoundError(f"Song directory does not exist: {song_dir}")

    audio_files = sorted(
        [p for p in song_dir.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
    )
    stems_sub = song_dir / "stems"
    if stems_sub.exists():
        audio_files.extend(
            sorted(p for p in stems_sub.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
        )
    if not audio_files:
        raise FileNotFoundError(
            f"No audio files (.wav/.ogg/.flac/.mp3) found in {song_dir}. "
            "Drop your audio or FL Studio stems there first."
        )

    stems = StemPaths()
    non_stem_candidates: list[Path] = []

    for p in audio_files:
        low = p.stem.lower()
        if "drum" in low and stems.drums is None:
            stems.drums = str(p)
        elif "vocal" in low and stems.vocals is None:
            stems.vocals = str(p)
        elif "bass" in low and stems.bass is None:
            stems.bass = str(p)
        elif any(k in low for k in ("instrument", "guitar", "other", "music")) and stems.instruments is None:
            stems.instruments = str(p)
        else:
            non_stem_candidates.append(p)

    # Prefer explicit mix file (audio.ogg/wav/mp3 or non-stem track); else build mix from stems
    main_audio: Path | None = None
    for preferred_name in ("audio", "mix", "full", "song"):
        for cand in non_stem_candidates:
            if cand.stem.lower() == preferred_name:
                main_audio = cand
                break
        if main_audio:
            break

    if main_audio is None and non_stem_candidates:
        # Prefer .ogg/.wav over .mp3 if multiple exist
        priority = {".ogg": 0, ".wav": 1, ".flac": 2, ".mp3": 3}
        non_stem_candidates.sort(key=lambda p: priority.get(p.suffix.lower(), 9))
        main_audio = non_stem_candidates[0]

    if main_audio is None:
        # User only dropped stems (e.g. from FL Studio) -> synthesize a clean mix_audio.wav
        main_audio = build_mix_from_stems(song_dir, stems)

    return main_audio, stems


def load_mono_audio(path: Path, target_sr: int | None = None) -> tuple[np.ndarray, int]:
    """Load any supported audio file as mono float32 without requiring external ffmpeg."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = np.mean(data, axis=1).astype(np.float32)
    if target_sr is not None and sr != target_sr:
        import librosa

        mono = librosa.resample(mono, orig_sr=sr, target_sr=target_sr).astype(np.float32)
        sr = target_sr
    return mono, sr


def get_audio_duration(path: Path) -> float:
    info = sf.info(str(path))
    return float(info.duration)


def build_mix_from_stems(song_dir: Path, stems: StemPaths) -> Path:
    """Sum available FL Studio stems into audio_mix.wav so OpenTaiko has one WAVE file."""
    stem_list = [Path(p) for p in (stems.drums, stems.vocals, stems.bass, stems.instruments) if p]
    if not stem_list:
        raise FileNotFoundError(f"Cannot build mix: no stems found in {song_dir}")

    mix_data = None
    mix_sr = 44100
    for sp in stem_list:
        audio, sr = sf.read(str(sp), dtype="float32", always_2d=True)
        if mix_data is None:
            mix_data = audio
            mix_sr = sr
        else:
            min_len = min(len(mix_data), len(audio))
            if len(audio) > len(mix_data):
                pad = np.zeros((len(audio) - len(mix_data), mix_data.shape[1]), dtype=np.float32)
                mix_data = np.vstack([mix_data, pad])
            mix_data[: len(audio)] += audio[:, : mix_data.shape[1]]

    peak = float(np.max(np.abs(mix_data))) if mix_data is not None else 1.0
    if peak > 0.98:
        mix_data = mix_data * (0.95 / peak)

    out_path = song_dir / "audio_mix.wav"
    sf.write(str(out_path), mix_data, mix_sr, subtype="PCM_16")
    return out_path


@contextmanager
def ensure_wav_path(audio_path: Path) -> Iterator[Path]:
    """Yield a readable PCM WAV path (converting .mp3/.ogg temporarily if needed)."""
    if audio_path.suffix.lower() == ".wav":
        yield audio_path
        return

    data, sr = sf.read(str(audio_path), dtype="float32", always_2d=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        sf.write(str(tmp_path), data, sr, subtype="PCM_16")
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)


def separate_stems_demucs(song_dir: Path, audio_path: Path, device: str = "cuda") -> StemPaths:
    """Split a single mix file into cached drums/vocals/bass/instruments WAVs using HTDemucs."""
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    stems_dir = song_dir / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)

    out_map = {
        "drums": stems_dir / "drums.wav",
        "vocals": stems_dir / "vocals.wav",
        "bass": stems_dir / "bass.wav",
        "other": stems_dir / "instruments.wav",
    }

    if all(p.exists() for p in out_map.values()):
        return StemPaths(
            drums=str(out_map["drums"]),
            vocals=str(out_map["vocals"]),
            bass=str(out_map["bass"]),
            instruments=str(out_map["other"]),
        )

    chosen_device = device if (device == "cuda" and torch.cuda.is_available()) else "cpu"
    print(f"[TaikoLab Demucs] Separating stems on {chosen_device.upper()} for {audio_path.name} (cached for future runs)...")

    model = get_model("htdemucs")
    model.to(chosen_device)
    model.eval()

    data, sr = sf.read(str(audio_path), dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    else:
        data = data[:, :2]

    if sr != model.samplerate:
        import librosa
        ch0 = librosa.resample(data[:, 0], orig_sr=sr, target_sr=model.samplerate)
        ch1 = librosa.resample(data[:, 1], orig_sr=sr, target_sr=model.samplerate)
        data = np.stack([ch0, ch1], axis=1).astype(np.float32)

    wav_tensor = torch.from_numpy(data.T).unsqueeze(0).to(chosen_device)
    with torch.no_grad():
        sources = apply_model(model, wav_tensor, device=chosen_device, split=True, overlap=0.25, progress=True)[0]

    sources_cpu = sources.cpu().numpy()
    for idx, src_name in enumerate(model.sources):
        if src_name in out_map:
            audio_np = sources_cpu[idx].T
            peak = float(np.max(np.abs(audio_np)))
            if peak > 0.99:
                audio_np = audio_np * (0.98 / peak)
            sf.write(str(out_map[src_name]), audio_np, model.samplerate, subtype="PCM_16")

    return StemPaths(
        drums=str(out_map["drums"]),
        vocals=str(out_map["vocals"]),
        bass=str(out_map["bass"]),
        instruments=str(out_map["other"]),
    )
