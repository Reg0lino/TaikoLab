"""Hybrid Drum Transcription: ADTOF-pytorch + 3-Band DSP fusion."""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import pretty_midi

from taikolab.ingest.drum_dsp import extract_dsp_drum_hits
from taikolab.ingest.stem_loader import ensure_wav_path
from taikolab.schemas import DrumClass, DrumHit

# Standard General MIDI drum pitch mapping used by ADTOF
_GM_DRUM_MAP: dict[int, DrumClass] = {
    35: "kick",
    36: "kick",
    37: "snare",
    38: "snare",
    39: "snare",
    40: "snare",
    42: "hihat",
    44: "hihat",
    46: "hihat",
    41: "tom",
    43: "tom",
    45: "tom",
    47: "tom",
    48: "tom",
    50: "tom",
    49: "crash",
    52: "crash",
    55: "crash",
    57: "crash",
    51: "ride",
    53: "ride",
    59: "ride",
}


def run_adtof_transcription(drums_path: Path, device: str = "cpu") -> list[DrumHit]:
    """Run ADTOF-pytorch on a drum stem or mix and return DrumHit events."""
    import torch
    from adtof_pytorch import transcribe_to_midi

    chosen_device = device if (device == "cuda" and torch.cuda.is_available()) else "cpu"

    with ensure_wav_path(drums_path) as wav_path:
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp_mid:
            mid_path = Path(tmp_mid.name)
        try:
            sig = inspect.signature(transcribe_to_midi)
            kwargs: dict = {}
            if "device" in sig.parameters:
                kwargs["device"] = chosen_device
            try:
                transcribe_to_midi(str(wav_path), str(mid_path), **kwargs)
            except RuntimeError as exc:
                if chosen_device == "cuda" and "cuda" in str(exc).lower() and "device" in sig.parameters:
                    print(f"[TaikoLab ADTOF] CUDA error ({exc}); retrying on CPU...")
                    kwargs["device"] = "cpu"
                    transcribe_to_midi(str(wav_path), str(mid_path), **kwargs)
                else:
                    raise
            pm = pretty_midi.PrettyMIDI(str(mid_path))
        finally:
            mid_path.unlink(missing_ok=True)

    hits: list[DrumHit] = []
    for inst in pm.instruments:
        for note in inst.notes:
            d_cls = _GM_DRUM_MAP.get(int(note.pitch), "unknown")
            vel = max(0.1, min(1.0, float(note.velocity) / 127.0))
            hits.append(
                DrumHit(
                    time_sec=round(float(note.start), 4),
                    drum_class=d_cls,
                    velocity=round(vel, 3),
                    source="adtof",
                    pitch=int(note.pitch),
                )
            )
    hits.sort(key=lambda h: h.time_sec)
    return hits


def transcribe_drums_hybrid(
    drums_path: Path,
    has_isolated_drums: bool = True,
    device: str = "cuda",
) -> list[DrumHit]:
    """Combine ADTOF classifications with 3-band DSP transients to fix missed double-kicks/slams."""
    adtof_hits = run_adtof_transcription(drums_path, device=device)

    # Only run raw DSP peak rescue on clean drum stems (avoids guitar bleed false positives)
    if not has_isolated_drums:
        return adtof_hits

    dsp_hits = extract_dsp_drum_hits(drums_path)
    merged: list[DrumHit] = [h.model_copy() for h in adtof_hits]

    # Rescue missed DSP kicks/snares/toms/crashes on isolated drum stems (including softer fills)
    for dh in dsp_hits:
        if dh.velocity < 0.36:
            continue
        matched = False
        for ah in merged:
            if ah.drum_class == dh.drum_class and abs(ah.time_sec - dh.time_sec) <= 0.038:
                ah.velocity = round(max(ah.velocity, dh.velocity), 3)
                ah.source = "hybrid"
                matched = True
                break
        if not matched and dh.drum_class in ("kick", "snare", "tom", "crash"):
            min_rescue_vel = 0.52 if dh.drum_class == "crash" else 0.40
            if dh.velocity >= min_rescue_vel:
                merged.append(dh)

    merged.sort(key=lambda h: h.time_sec)
    return merged