"""Audio & FL Studio stem discovery, safe WAV loading, HPSS, and GTX-1080-paced Demucs."""

from __future__ import annotations

import gc
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from taikolab.schemas import StemPaths

AUDIO_EXTS = (".wav", ".ogg", ".flac", ".mp3")


def is_pascal_or_older_gpu() -> tuple[bool, str]:
    """Return (True, gpu_name) when CUDA device is Pascal (sm_6x) or older, which crashes on cu126 Demucs."""
    import torch

    if not torch.cuda.is_available():
        return False, ""
    try:
        major, minor = torch.cuda.get_device_capability(0)
        name = torch.cuda.get_device_name(0)
        return major < 7, f"{name} (sm_{major}{minor})"
    except Exception:
        return False, ""


def configure_runtime_threads(max_threads: int | None = None) -> int:
    """Cap CPU threads so ADTOF/HPSS/SyncToolbox keep Windows UI & audio responsive."""
    import torch

    if max_threads is None:
        cores = os.cpu_count() or 4
        max_threads = min(6, max(2, cores // 2))
    torch.set_num_threads(max_threads)
    return max_threads


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

    main_audio: Path | None = None
    for preferred_name in ("audio", "mix", "full", "song"):
        for cand in non_stem_candidates:
            if cand.stem.lower() == preferred_name:
                main_audio = cand
                break
        if main_audio:
            break

    if main_audio is None and non_stem_candidates:
        priority = {".ogg": 0, ".wav": 1, ".flac": 2, ".mp3": 3}
        non_stem_candidates.sort(key=lambda p: priority.get(p.suffix.lower(), 9))
        main_audio = non_stem_candidates[0]

    if main_audio is None:
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


@contextmanager
def temporary_hpss_split_wavs(audio_path: Path, margin: float = 2.2) -> Iterator[tuple[Path, Path]]:
    """Create temporary CPU HPSS (percussive_wav, harmonic_wav) outside song_dir (0 MB VRAM)."""
    import librosa

    configure_runtime_threads()
    y, sr = load_mono_audio(audio_path, target_sr=44100)
    if len(y) > sr * 0.25 and float(np.max(np.abs(y))) > 1e-5:
        y_harm, y_perc = librosa.effects.hpss(y, margin=margin)
    else:
        y_harm, y_perc = y, y

    def _norm(arr: np.ndarray) -> np.ndarray:
        pk = float(np.max(np.abs(arr))) if len(arr) else 0.0
        return (arr * (0.95 / pk) if pk > 0.98 else arr).astype(np.float32)

    with (
        tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_p,
        tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_h,
    ):
        p_path = Path(tmp_p.name)
        h_path = Path(tmp_h.name)
    try:
        sf.write(str(p_path), _norm(y_perc), sr, subtype="PCM_16")
        sf.write(str(h_path), _norm(y_harm), sr, subtype="PCM_16")
        yield p_path, h_path
    finally:
        p_path.unlink(missing_ok=True)
        h_path.unlink(missing_ok=True)


@contextmanager
def temporary_hpss_percussive_wav(audio_path: Path, margin: float = 2.2) -> Iterator[Path]:
    """Create a temporary CPU HPSS percussive WAV outside song_dir (0 MB VRAM)."""
    import librosa

    configure_runtime_threads()
    y, sr = load_mono_audio(audio_path, target_sr=44100)
    if len(y) > sr * 0.25 and float(np.max(np.abs(y))) > 1e-5:
        _, y_perc = librosa.effects.hpss(y, margin=margin)
    else:
        y_perc = y
    peak = float(np.max(np.abs(y_perc))) if len(y_perc) else 0.0
    if peak > 0.98:
        y_perc = y_perc * (0.95 / peak)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        sf.write(str(tmp_path), y_perc.astype(np.float32), sr, subtype="PCM_16")
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)


def _load_demucs_model(model_name: str, chosen_device: str):
    from demucs.pretrained import get_model

    requested = model_name if model_name in ("htdemucs", "mdx_q", "mdx_extra_q", "mdx") else "htdemucs"
    try:
        model = get_model(requested)
    except Exception as exc:
        if requested != "htdemucs":
            print(f"[TaikoLab Demucs] Could not load '{requested}' ({exc}); falling back to 'htdemucs'.")
            model = get_model("htdemucs")
        else:
            raise
    model.float()
    model.to(chosen_device)
    model.eval()
    return model


def _prepare_stereo_for_model(audio_path: Path, target_sr: int) -> np.ndarray:
    data, sr = sf.read(str(audio_path), dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    else:
        data = data[:, :2]
    if sr != target_sr:
        import librosa

        ch0 = librosa.resample(data[:, 0], orig_sr=sr, target_sr=target_sr)
        ch1 = librosa.resample(data[:, 1], orig_sr=sr, target_sr=target_sr)
        data = np.stack([ch0, ch1], axis=1).astype(np.float32)
    return data


def _run_paced_demucs_segments(
    model,
    data: np.ndarray,
    intervals: list[tuple[int, int]],
    chosen_device: str,
    chunk_sec: float = 6.5,
    overlap_sec: float = 1.5,
    pause_sec: float = 0.28,
    progress_label: str = "Separated",
) -> np.ndarray:
    """Execute single-segment CUDA Demucs passes (split=False, shifts=0) with WDDM yields."""
    import torch
    from demucs.apply import apply_model

    sr_model = int(model.samplerate)
    source_names = list(model.sources)
    total_samples = len(data)
    win_samples = int(chunk_sec * sr_model)
    overlap_samples = int(overlap_sec * sr_model)
    step = max(1, win_samples - overlap_samples)

    acc = np.zeros((len(source_names), total_samples, 2), dtype=np.float32)
    weight_acc = np.zeros((total_samples, 1), dtype=np.float32)

    slices: list[tuple[int, int]] = []
    for iv_start, iv_end in intervals:
        iv_s = max(0, iv_start)
        iv_e = min(total_samples, iv_end)
        if iv_e - iv_s <= int(0.5 * sr_model):
            continue
        if iv_e - iv_s <= win_samples:
            slices.append((iv_s, iv_e))
            continue
        pos = iv_s
        while pos < iv_e:
            end = min(iv_e, pos + win_samples)
            if end - pos < int(1.0 * sr_model) and slices:
                break
            slices.append((pos, end))
            if end >= iv_e:
                break
            pos += step

    if chosen_device == "cuda":
        torch.backends.cudnn.benchmark = False

    total_chunks = max(1, len(slices))
    for chunk_idx, (start, end) in enumerate(slices):
        chunk = data[start:end]
        wav_tensor = torch.from_numpy(chunk.T).unsqueeze(0).to(chosen_device, dtype=torch.float32)
        with torch.no_grad():
            out_t = apply_model(
                model,
                wav_tensor,
                device=chosen_device,
                shifts=0,
                split=False,
                overlap=0.25,
                progress=False,
            )[0]
        out_np = out_t.cpu().numpy()
        del wav_tensor, out_t

        chunk_len = end - start
        w = np.ones((chunk_len, 1), dtype=np.float32)
        if chunk_idx > 0 and start > 0:
            fade_n = min(overlap_samples, chunk_len // 2)
            if fade_n > 0:
                w[:fade_n, 0] = np.linspace(0.0, 1.0, fade_n, dtype=np.float32)
        if end < total_samples:
            fade_n = min(overlap_samples, chunk_len // 2)
            if fade_n > 0:
                w[-fade_n:, 0] = np.linspace(1.0, 0.0, fade_n, dtype=np.float32)

        for s_i in range(len(source_names)):
            acc[s_i, start:end, :] += out_np[s_i].T * w
        weight_acc[start:end, :] += w

        if chosen_device == "cuda":
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            time.sleep(pause_sec)

        pct = int(round(100.0 * (chunk_idx + 1) / total_chunks))
        print(
            f"[TaikoLab Demucs] {progress_label} {pct}% (chunk {chunk_idx + 1}/{total_chunks}: "
            f"{start / sr_model:.1f}s-{end / sr_model:.1f}s)",
            flush=True,
        )

    safe_w = np.maximum(weight_acc, 1e-6)
    return np.stack([(acc[s_i] / safe_w) for s_i in range(len(source_names))], axis=0)


def separate_stems_demucs(
    song_dir: Path,
    audio_path: Path,
    device: str = "cuda",
    model_name: str = "htdemucs",
) -> StemPaths:
    """Split mix into cached stems using paced single-segment passes safe for GTX 1080 WDDM."""
    import torch

    configure_runtime_threads()
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
    if chosen_device == "cuda" and os.environ.get("TAIKOLAB_FORCE_PASCAL_CUDA") != "1":
        is_old, gpu_label = is_pascal_or_older_gpu()
        if is_old:
            raise RuntimeError(
                f"Blocked CUDA Demucs on Pascal GPU {gpu_label} to prevent driver/PC crash; using CPU HPSS instead."
            )
    print(
        f"[TaikoLab Demucs] Separating stems ({model_name}) on {chosen_device.upper()} "
        f"for {audio_path.name} (cached for future runs)..."
    )

    model = _load_demucs_model(model_name, chosen_device)
    sr_model = int(model.samplerate)
    source_names = list(model.sources)
    data = _prepare_stereo_for_model(audio_path, sr_model)

    try:
        sources_cpu = _run_paced_demucs_segments(
            model,
            data,
            intervals=[(0, len(data))],
            chosen_device=chosen_device,
            chunk_sec=6.5,
            overlap_sec=1.5,
            pause_sec=0.28,
            progress_label="Separated",
        )
    finally:
        del model
        gc.collect()
        if chosen_device == "cuda":
            torch.cuda.synchronize()
            torch.cuda.empty_cache()

    for idx, src_name in enumerate(source_names):
        if src_name in out_map:
            audio_np = sources_cpu[idx]
            if audio_np.ndim == 2 and audio_np.shape[0] == 2 and audio_np.shape[1] != 2:
                audio_np = audio_np.T
            peak = float(np.max(np.abs(audio_np)))
            if peak > 0.99:
                audio_np = audio_np * (0.98 / peak)
            sf.write(str(out_map[src_name]), audio_np, sr_model, subtype="PCM_16")

    return StemPaths(
        drums=str(out_map["drums"]),
        vocals=str(out_map["vocals"]),
        bass=str(out_map["bass"]),
        instruments=str(out_map["other"]),
    )


@contextmanager
def temporary_anchor_drums_wav(
    audio_path: Path,
    focus_times: list[float],
    device: str = "cuda",
    model_name: str = "htdemucs",
    window_sec: float = 12.0,
) -> Iterator[tuple[Path, list[tuple[float, float]]]]:
    """Run paced GPU Demucs only on 2-3 targeted 12s windows (<6s GPU total, ~616 MB VRAM)."""
    import torch

    configure_runtime_threads()
    chosen_device = device if (device == "cuda" and torch.cuda.is_available()) else "cpu"
    if chosen_device == "cuda" and os.environ.get("TAIKOLAB_FORCE_PASCAL_CUDA") != "1":
        is_old, gpu_label = is_pascal_or_older_gpu()
        if is_old:
            raise RuntimeError(
                f"Blocked CUDA Demucs on Pascal GPU {gpu_label} to prevent driver/PC crash; using CPU HPSS instead."
            )
    model = _load_demucs_model(model_name, chosen_device)
    sr_model = int(model.samplerate)
    source_names = list(model.sources)
    data = _prepare_stereo_for_model(audio_path, sr_model)
    dur = len(data) / float(sr_model)

    half_w = window_sec * 0.5
    raw_iv = sorted(
        (max(0.0, t - half_w), min(dur, t + half_w))
        for t in focus_times
        if 0.0 <= t <= dur
    )
    merged_sec: list[tuple[float, float]] = []
    for s, e in raw_iv:
        if e - s < 2.0:
            continue
        if merged_sec and s <= merged_sec[-1][1] + 1.0:
            merged_sec[-1] = (merged_sec[-1][0], max(merged_sec[-1][1], e))
        else:
            merged_sec.append((s, e))

    if not merged_sec:
        merged_sec = [(0.0, min(dur, window_sec))]

    sample_intervals = [(int(s * sr_model), int(e * sr_model)) for s, e in merged_sec]
    print(
        f"[TaikoLab Demucs] Anchor GPU ({model_name}) on {len(sample_intervals)} window(s) "
        f"[{', '.join(f'{s:.1f}-{e:.1f}s' for s, e in merged_sec)}]..."
    )

    try:
        sources_cpu = _run_paced_demucs_segments(
            model,
            data,
            intervals=sample_intervals,
            chosen_device=chosen_device,
            chunk_sec=6.5,
            overlap_sec=1.5,
            pause_sec=0.20,
            progress_label="Anchors",
        )
    finally:
        del model
        gc.collect()
        if chosen_device == "cuda":
            torch.cuda.synchronize()
            torch.cuda.empty_cache()

    drum_idx = source_names.index("drums") if "drums" in source_names else 0
    drums_np = sources_cpu[drum_idx]
    if drums_np.ndim == 2 and drums_np.shape[0] == 2 and drums_np.shape[1] != 2:
        drums_np = drums_np.T
    peak = float(np.max(np.abs(drums_np)))
    if peak > 0.99:
        drums_np = drums_np * (0.98 / peak)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        sf.write(str(tmp_path), drums_np, sr_model, subtype="PCM_16")
        yield tmp_path, merged_sec
    finally:
        tmp_path.unlink(missing_ok=True)