"""Guitar/synth riff injection & Big-Note accent fusion."""

from __future__ import annotations

from taikolab.arranger.mapper_drums import PlacedCandidate
from taikolab.arranger.quantizer import MeasureSlot, time_to_measure_slot
from taikolab.arranger.section_builder import MeasurePlan
from taikolab.config import AppConfig
from taikolab.schemas import OnsetEvent, TaikoNote


def apply_guitar_fusion(
    placed: dict[tuple[int, int], PlacedCandidate],
    guitar_events: list[OnsetEvent],
    slots: list[MeasureSlot],
    plans: list[MeasurePlan],
    cfg: AppConfig,
) -> dict[tuple[int, int], PlacedCandidate]:
    """Fuse guitar accents into drum pockets, while simplifying quiet guitar-only bars."""
    merged = dict(placed)

    for ev in guitar_events:
        loc = time_to_measure_slot(ev.time_sec, slots)
        if loc is None:
            continue
        m_idx, s_idx = loc
        plan = plans[m_idx]
        if plan.follow_mode not in ("hybrid", "guitar", "vocals_guitar", "all_balanced"):
            continue
        if plan.low_energy_guitar:
            continue
        if ev.strength < cfg.preset.guitar_min_strength:
            continue

        key = (m_idx, s_idx)
        existing = merged.get(key)

        if existing is not None:
            if plan.enable_big_notes and ev.strength >= cfg.preset.big_note_threshold:
                if existing.note_type == TaikoNote.DON:
                    existing.note_type = TaikoNote.BIG_DON
                elif existing.note_type == TaikoNote.KAT:
                    existing.note_type = TaikoNote.BIG_KAT
                existing.priority += 0.2
            continue

        bar_drum_count = sum(1 for (bm, _) in merged if bm == m_idx)
        adjacent_busy = any((m_idx, s_idx + delta) in merged for delta in (-1, 1))
        if plan.follow_mode in ("guitar", "vocals_guitar") or (
            bar_drum_count < 6 and not adjacent_busy and ev.strength >= max(cfg.preset.guitar_min_strength, 0.65)
        ):
            note = (
                TaikoNote.BIG_DON
                if plan.enable_big_notes and ev.strength >= cfg.preset.big_note_threshold
                else (TaikoNote.KAT if s_idx % 4 == 2 and bar_drum_count > 0 else TaikoNote.DON)
            )
            merged[key] = PlacedCandidate(
                measure_idx=m_idx,
                slot_idx=s_idx,
                time_sec=ev.time_sec,
                note_type=note,
                priority=0.65 * ev.strength,
                source="guitar",
            )

    for slot, plan in zip(slots, plans):
        if not plan.low_energy_guitar or plan.quiet_guitar_behavior != "downbeat":
            continue
        measure_idx = slot.measure_idx
        if any(m_idx == measure_idx for m_idx, _ in placed):
            continue

        key = (measure_idx, 0)
        merged[key] = PlacedCandidate(
            measure_idx=measure_idx,
            slot_idx=0,
            time_sec=slot.start_sec,
            note_type=TaikoNote.DON,
            priority=0.65,
            source="guitar:downbeat",
        )

    return merged