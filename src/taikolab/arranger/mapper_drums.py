"""Drum-driven Taiko mapping (Kick=Don, Snare=Kat, Low/High Tom alternation)."""

from __future__ import annotations

from dataclasses import dataclass

from taikolab.arranger.quantizer import MeasureSlot, time_to_measure_slot
from taikolab.arranger.section_builder import MeasurePlan
from taikolab.config import AppConfig
from taikolab.schemas import DrumHit, TaikoNote, TaikoTarget


@dataclass
class PlacedCandidate:
    measure_idx: int
    slot_idx: int
    time_sec: float
    note_type: int
    priority: float
    source: str


def _target_to_note(target: TaikoTarget, velocity: float, cfg: AppConfig, enable_big: bool) -> int:
    if target == "ignore":
        return TaikoNote.REST
    if target == "big_don":
        return TaikoNote.BIG_DON if enable_big else TaikoNote.DON
    if target == "big_kat":
        return TaikoNote.BIG_KAT if enable_big else TaikoNote.KAT
    if target == "don":
        if enable_big and velocity >= min(0.98, cfg.preset.big_note_threshold + 0.08):
            return TaikoNote.BIG_DON
        return TaikoNote.DON
    if target == "kat":
        if enable_big and velocity >= min(0.98, cfg.preset.big_note_threshold + 0.08):
            return TaikoNote.BIG_KAT
        return TaikoNote.KAT
    return TaikoNote.REST


def _drum_priority(hit: DrumHit) -> float:
    base = {
        "snare": 1.0,
        "kick": 0.95,
        "crash": 0.92,
        "tom": 0.82,
        "ride": 0.25,
        "hihat": 0.20,
        "unknown": 0.50,
    }.get(hit.drum_class, 0.50)
    return base * (0.5 + 0.5 * hit.velocity)


def map_drum_hits(
    drum_hits: list[DrumHit],
    slots: list[MeasureSlot],
    plans: list[MeasurePlan],
    cfg: AppConfig,
) -> dict[tuple[int, int], PlacedCandidate]:
    """Map DrumHits onto quantized cells: Kick=Don, Snare=Kat, Low/High Tom alternation."""
    placed: dict[tuple[int, int], PlacedCandidate] = {}

    for hit in drum_hits:
        loc = time_to_measure_slot(hit.time_sec, slots)
        if loc is None:
            continue
        m_idx, s_idx = loc
        plan = plans[m_idx]

        if plan.follow_mode == "vocals":
            continue

        eff_vel = min(1.0, hit.velocity * plan.drum_sensitivity)
        if hit.drum_class in ("hihat", "ride") and cfg.preset.drum_mapping.get(hit.drum_class) == "ignore":
            continue
        if eff_vel < 0.25:
            continue

        target = cfg.preset.drum_mapping.get(hit.drum_class, "don")
        note = _target_to_note(target, eff_vel, cfg, plan.enable_big_notes)
        if note == TaikoNote.REST:
            continue

        prio = _drum_priority(hit) * plan.drum_sensitivity
        key = (m_idx, s_idx)
        existing = placed.get(key)

        if existing is None:
            placed[key] = PlacedCandidate(
                measure_idx=m_idx,
                slot_idx=s_idx,
                time_sec=hit.time_sec,
                note_type=note,
                priority=prio,
                source=f"drum:{hit.drum_class}",
            )
        else:
            pair = {existing.source, f"drum:{hit.drum_class}"}
            # Kick + Crash/Snare collision: promote to Big Note if enabled
            if plan.enable_big_notes and "drum:kick" in pair and ("drum:crash" in pair or "drum:snare" in pair):
                if hit.velocity >= cfg.preset.big_note_threshold or existing.priority >= 0.85:
                    existing.note_type = TaikoNote.BIG_KAT if "drum:crash" in pair else TaikoNote.BIG_DON
                    existing.priority = max(existing.priority, prio) + 0.15
                    existing.source = "drum:slam"
                    continue

            # In a punk beat, Kick and Snare take absolute priority over cymbals
            if prio > existing.priority:
                placed[key] = PlacedCandidate(
                    measure_idx=m_idx,
                    slot_idx=s_idx,
                    time_sec=hit.time_sec,
                    note_type=note,
                    priority=prio,
                    source=f"drum:{hit.drum_class}",
                )

    return placed