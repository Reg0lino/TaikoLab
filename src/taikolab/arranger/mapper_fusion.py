"""Guitar/bass riff injection & Big-Note accent fusion with sub-bar section support."""

from __future__ import annotations

from taikolab.arranger.mapper_drums import PlacedCandidate
from taikolab.arranger.quantizer import MeasureSlot, time_to_measure_slot
from taikolab.arranger.section_builder import MeasurePlan, resolve_follow_mode_at_time
from taikolab.config import AppConfig
from taikolab.schemas import OnsetEvent, SongOverrides, TaikoNote


def apply_guitar_fusion(
    placed: dict[tuple[int, int], PlacedCandidate],
    guitar_events: list[OnsetEvent],
    slots: list[MeasureSlot],
    plans: list[MeasurePlan],
    cfg: AppConfig,
    overrides: SongOverrides | None = None,
) -> dict[tuple[int, int], PlacedCandidate]:
    """Fuse guitar/bass accents into pockets, and phrase solo bass/guitar riffs cleanly."""
    merged = dict(placed)
    solo_toggle = False

    for ev in sorted(guitar_events, key=lambda e: e.time_sec):
        loc = time_to_measure_slot(ev.time_sec, slots)
        if loc is None:
            continue
        m_idx, s_idx = loc
        plan = plans[m_idx]
        eff_mode = resolve_follow_mode_at_time(ev.time_sec, plan, overrides)
        if eff_mode not in ("hybrid", "guitar", "vocals_guitar", "all_balanced"):
            continue
        if plan.low_energy_guitar:
            continue

        min_str = min(cfg.preset.guitar_min_strength, 0.35) if eff_mode == "guitar" else cfg.preset.guitar_min_strength
        if ev.strength < min_str:
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
        if eff_mode in ("guitar", "vocals_guitar") or (
            bar_drum_count < 6 and not adjacent_busy and ev.strength >= max(cfg.preset.guitar_min_strength, 0.65)
        ):
            if eff_mode == "guitar":
                beat_step = max(1, slots[m_idx].grid_size // max(1, slots[m_idx].numerator))
                is_beat = (s_idx % beat_step) == 0
                if plan.enable_big_notes and ev.strength >= cfg.preset.big_note_threshold and s_idx == 0:
                    note = TaikoNote.BIG_DON
                elif is_beat and not solo_toggle:
                    note = TaikoNote.DON
                else:
                    solo_toggle = not solo_toggle
                    note = TaikoNote.KAT if solo_toggle else TaikoNote.DON
            else:
                note = (
                    TaikoNote.BIG_DON
                    if plan.enable_big_notes and ev.strength >= cfg.preset.big_note_threshold and s_idx == 0
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
