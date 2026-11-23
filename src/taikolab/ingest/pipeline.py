"""Stage 1 Audio Ingest Orchestrator: turns audio/stems into cached analysis.json."""

from __future__ import annotations

import gc
from pathlib import Path

from taikolab.ingest.beat_tracker import track_beats_and_regions
from taikolab.ingest.drum_transcriber import run_adtof_transcription, transcribe_drums_hybrid
from taikolab.ingest.stem_loader import (
    configure_runtime_threads,
    discover_song_files,
    get_audio_duration,
    is_pascal_or_older_gpu,
    separate_stems_demucs,
    temporary_hpss_split_wavs,
)
from taikolab.ingest.vocal_riff_tracker import (
    compute_energy_curve,
    ensure_energy_curve,
    extract_guitar_events,
    extract_vocal_events,
)
from taikolab.schemas import SongAnalysis


def analyze_song_dir(
    song_id: str,
    song_dir: Path,
    title: str | None = None,
    artist: str = "Unknown",
    device: str = "cuda",
    demucs_model: str = "htdemucs",
    use_demucs: bool = True,
) -> SongAnalysis:
    """Analyze audio/stems in song_dir and save analysis.json safely."""
    configure_runtime_threads()
    main_audio, stems = discover_song_files(song_dir)
    duration = get_audio_duration(main_audio)

    effective_device = device
    if effective_device == "cuda":
        is_old, gpu_label = is_pascal_or_older_gpu()
        if is_old:
            print(f"[TaikoLab Ingest] Pascal GPU {gpu_label} detected; running safe CPU HPSS mode instead of CUDA Demucs.")
            effective_device = "cpu"
            use_demucs = False

    if not stems.drums and use_demucs:
        try:
            stems = separate_stems_demucs(song_dir, main_audio, device=effective_device, model_name=demucs_model)
            gc.collect()
        except Exception as exc:
            print(f"[TaikoLab Ingest] Demucs skipped ({exc}); using fast CPU HPSS fallback.")

    print(f"[TaikoLab Ingest] Audio: {main_audio.name} ({duration:.1f}s)")
    print(
        f"[TaikoLab Ingest] Stems ready -> drums={bool(stems.drums)}, "
        f"vocals={bool(stems.vocals)}, instruments={bool(stems.instruments)}"
    )

    beat_source = main_audio if main_audio.exists() else Path(stems.drums)  # type: ignore[arg-type]
    print("[TaikoLab Ingest] Tracking beats & downbeats (Beat This!)...")
    beats, regions = track_beats_and_regions(beat_source, duration, device=effective_device)
    print(f"  -> Found {len(beats)} beats, {len(regions)} tempo regions.")

    vocal_events = []
    guitar_events = []
    if stems.drums:
        print("[TaikoLab Ingest] Transcribing isolated drums (ADTOF-pytorch CPU + 3-Band DSP)...")
        drum_hits = transcribe_drums_hybrid(Path(stems.drums), has_isolated_drums=True, device="cpu")
        if stems.vocals:
            vocal_events = extract_vocal_events(Path(stems.vocals))
        guitar_src = stems.instruments or stems.bass
        if guitar_src:
            guitar_events = extract_guitar_events(Path(guitar_src))
    else:
        print("[TaikoLab Ingest] Extracting percussive/harmonic layers via CPU HPSS (0 MB VRAM)...")
        with temporary_hpss_split_wavs(main_audio) as (perc_wav, harm_wav):
            drum_hits = run_adtof_transcription(perc_wav, device="cpu")
            vocal_events = extract_vocal_events(harm_wav)
            guitar_events = extract_guitar_events(harm_wav)

    print(f"  -> Extracted {len(drum_hits)} drums, {len(vocal_events)} vocals, {len(guitar_events)} guitars.")

    energy = compute_energy_curve(stems, duration) if any((stems.drums, stems.vocals, stems.instruments, stems.bass)) else []
    analysis = SongAnalysis(
        song_id=song_id,
        title=title or song_id.replace("_", " ").title(),
        artist=artist,
        audio_path=main_audio.name,
        duration_sec=round(duration, 3),
        stems=stems,
        beats=beats,
        tempo_regions=regions,
        drum_hits=drum_hits,
        vocal_events=vocal_events,
        guitar_events=guitar_events,
        energy_curve=energy,
    )
    ensure_energy_curve(analysis, song_dir=song_dir)

    out_json = song_dir / "analysis.json"
    out_json.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    print(f"[TaikoLab Ingest] Saved cached analysis -> {out_json}")
    return analysis