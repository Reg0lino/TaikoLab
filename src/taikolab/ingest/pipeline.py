"""Stage 1 Audio Ingest Orchestrator: turns audio/stems into cached analysis.json."""

from __future__ import annotations

from pathlib import Path

from taikolab.ingest.beat_tracker import track_beats_and_regions
from taikolab.ingest.drum_transcriber import transcribe_drums_hybrid
from taikolab.ingest.stem_loader import discover_song_files, get_audio_duration, separate_stems_demucs
from taikolab.ingest.vocal_riff_tracker import (
    compute_energy_curve,
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
) -> SongAnalysis:
    """Analyze audio/stems in song_dir and save analysis.json."""
    main_audio, stems = discover_song_files(song_dir)
    if not stems.drums:
        stems = separate_stems_demucs(song_dir, main_audio, device=device)

    duration = get_audio_duration(main_audio)

    print(f"[TaikoLab Ingest] Audio: {main_audio.name} ({duration:.1f}s)")
    print(f"[TaikoLab Ingest] Stems ready -> drums={bool(stems.drums)}, vocals={bool(stems.vocals)}, instruments={bool(stems.instruments)}")

    beat_source = main_audio if main_audio.exists() else Path(stems.drums)  # type: ignore[arg-type]
    print("[TaikoLab Ingest] Tracking beats & downbeats (Beat This!)...")
    beats, regions = track_beats_and_regions(beat_source, duration, device=device)
    print(f"  -> Found {len(beats)} beats, {len(regions)} tempo regions.")

    drum_source = Path(stems.drums) if stems.drums else main_audio
    print("[TaikoLab Ingest] Transcribing drums (ADTOF-pytorch + 3-Band DSP)...")
    drum_hits = transcribe_drums_hybrid(
        drum_source,
        has_isolated_drums=bool(stems.drums),
        device=device,
    )
    print(f"  -> Extracted {len(drum_hits)} drum hits.")

    vocal_events = []
    if stems.vocals:
        print("[TaikoLab Ingest] Extracting vocal cadence...")
        vocal_events = extract_vocal_events(Path(stems.vocals))
        print(f"  -> Extracted {len(vocal_events)} vocal events.")

    guitar_events = []
    guitar_src = stems.instruments or stems.bass
    if guitar_src:
        print("[TaikoLab Ingest] Extracting guitar/riff accents...")
        guitar_events = extract_guitar_events(Path(guitar_src))
        print(f"  -> Extracted {len(guitar_events)} guitar/bass events.")

    energy = compute_energy_curve(stems, duration)

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

    out_json = song_dir / "analysis.json"
    out_json.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    print(f"[TaikoLab Ingest] Saved cached analysis -> {out_json}")
    return analysis