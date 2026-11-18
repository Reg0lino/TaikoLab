"""Guitar riff injection & Big-Note accent fusion."""

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
    """Promote aligned guitar+drum hits to Big Notes and inject riff hits on sparse bars."""
    merged = dict(placed)

    for ev in guitar_events:
        loc = time_to_measure_slot(ev.time_sec, slots)
        if loc is None:
            continue
        m_idx, s_idx = loc
        plan = plans[m_idx]
        if plan.follow_mode not in ("hybrid", "guitar"):
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
        if plan.follow_mode == "guitar" or bar_drum_count < 4:
            note = TaikoNote.BIG_DON if (plan.enable_big_notes and ev.strength >= 0.85) else TaikoNote.DON
            merged[key] = PlacedCandidate(
                measure_idx=m_idx,
                slot_idx=s_idx,
                time_sec=ev.time_sec,
                note_type=note,
                priority=0.65 * ev.strength,
                source="guitar",
            )

    return merged