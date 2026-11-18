"""Hori Taiko USB Drum Ergonomics Filter (Intelligent Rolls & Kantan Easy Pauses)."""

from __future__ import annotations

from bisect import bisect_left

from taikolab.arranger.mapper_drums import PlacedCandidate
from taikolab.arranger.quantizer import MeasureSlot
from taikolab.arranger.section_builder import MeasurePlan
from taikolab.config import AppConfig
from taikolab.schemas import ChartMeasure, TaikoNote


def _slot_time(slot: MeasureSlot, s_idx: int) -> float:
    dur = slot.end_sec - slot.start_sec
    return slot.start_sec + (s_idx / float(slot.grid_size)) * dur


def _is_on_strong_subdivision(s_idx: int, grid_size: int) -> bool:
    step = max(1, grid_size // 4)
    return (s_idx % step) == 0


def _identify_drumroll_bars(
    by_measure: list[dict[int, PlacedCandidate]],
    slots: list[MeasureSlot],
    plans: list[MeasurePlan],
    is_oni: bool = False,
) -> set[int]:
    """Identify genuine buzz rolls and sustained fills for drumrolls."""
    roll_bars: set[int] = set()

    for m_idx, (slot, plan) in enumerate(zip(slots, plans)):
        if plan.follow_mode == "chaos_roll":
            roll_bars.add(m_idx)
            continue

        bar_dict = by_measure[m_idx]
        bar_dur = slot.end_sec - slot.start_sec
        if bar_dur < 0.60:
            continue

        # In Oni mode, players want to hit actual drum fills! Only make a roll if it's a genuine rapid buzz roll (8+ hits)
        if is_oni:
            if len(bar_dict) >= 12 and slot.bpm >= 215.0 and plan.fold_blasts_to_rolls:
                roll_bars.add(m_idx)
            continue

        # For Hard, Normal, and Easy: convert dense rapid fills into yellow drumrolls
        if len(bar_dict) >= 6:
            late_hits = [s for s in sorted(bar_dict.keys()) if s >= slot.grid_size // 2]
            fill_hits = sum(
                1 for s in late_hits if any(k in bar_dict[s].source for k in ("tom", "snare", "slam"))
            )
            # Pick phrase-ending fills (end of 4-bar or 8-bar cycles)
            if fill_hits >= 4 and (m_idx % 4 == 3 or m_idx in (7, 15, 31, 39, 47, 71, 95)):
                roll_bars.add(m_idx)

    # Ensure Easy and Normal have at least 2–3 fun rolls across the track
    if not is_oni and len(roll_bars) < 2 and len(slots) >= 16:
        candidate_bars = [i for i, b in enumerate(by_measure) if len(b) >= 5 and 6 <= i < len(slots) - 4]
        for b_idx in candidate_bars[:3]:
            roll_bars.add(b_idx)

    return roll_bars


def apply_hori_ergonomics(
    candidates: dict[tuple[int, int], PlacedCandidate],
    slots: list[MeasureSlot],
    plans: list[MeasurePlan],
    cfg: AppConfig,
) -> list[ChartMeasure]:
    """Apply physical Hori limits, intelligent rolls & authentic Easy mode pauses."""
    by_measure: list[dict[int, PlacedCandidate]] = [{} for _ in slots]
    for (m_idx, s_idx), cand in candidates.items():
        if 0 <= m_idx < len(slots):
            by_measure[m_idx][s_idx] = cand

    is_easy = (plans[0].max_nps <= 2.6) if plans else False
    is_oni = (plans[0].max_nps >= 9.0) if plans else False

    roll_measures = _identify_drumroll_bars(by_measure, slots, plans, is_oni=is_oni)

    # Easy Mode (Kantan) Phrasing: 2 bars of quarter-note play followed by 1-2 bars of complete rest
    if is_easy:
        active_streak = 0
        for m_idx in range(len(slots)):
            if m_idx in roll_measures:
                active_streak = 0
                continue
            if len(by_measure[m_idx]) > 0:
                active_streak += 1
                if active_streak > 2:
                    by_measure[m_idx] = {}
                    active_streak = 0
            else:
                active_streak = 0

    items: list[tuple[float, float, int, int, PlacedCandidate]] = []
    for m_idx, bar_dict in enumerate(by_measure):
        if m_idx in roll_measures:
            continue
        grid_sz = slots[m_idx].grid_size
        for s_idx, cand in bar_dict.items():
            t = _slot_time(slots[m_idx], s_idx)
            beat_bonus = 0.50 if _is_on_strong_subdivision(s_idx, grid_sz) else 0.0
            items.append((cand.priority + beat_bonus, t, m_idx, s_idx, cand))

    items.sort(key=lambda x: (-x[0], x[1]))

    accepted_times: list[float] = []
    kept: list[tuple[float, int, int, PlacedCandidate]] = []
    for _, t, m_idx, s_idx, cand in items:
        min_gap_sec = plans[m_idx].min_gap_ms / 1000.0
        pos = bisect_left(accepted_times, t)
        too_close = False
        if pos > 0 and (t - accepted_times[pos - 1]) < min_gap_sec:
            too_close = True
        if pos < len(accepted_times) and (accepted_times[pos] - t) < min_gap_sec:
            too_close = True
        if not too_close:
            accepted_times.insert(pos, t)
            kept.append((t, m_idx, s_idx, cand))

    kept.sort(key=lambda x: x[0])

    last_big_time = -10.0
    for t, _, _, cand in kept:
        if cand.note_type in (TaikoNote.BIG_DON, TaikoNote.BIG_KAT):
            if (t - last_big_time) < 0.45:
                cand.note_type = TaikoNote.DON if cand.note_type == TaikoNote.BIG_DON else TaikoNote.KAT
            else:
                last_big_time = t

    filtered_by_measure: list[dict[int, PlacedCandidate]] = [{} for _ in slots]
    for _, m_idx, s_idx, cand in kept:
        filtered_by_measure[m_idx][s_idx] = cand

    for m_idx, (slot, plan) in enumerate(zip(slots, plans)):
        if m_idx in roll_measures:
            continue
        bar_dict = filtered_by_measure[m_idx]
        bar_dur = max(0.05, slot.end_sec - slot.start_sec)
        max_notes = max(1, int(round(plan.max_nps * bar_dur)))

        while len(bar_dict) > max_notes:
            worst_key = min(
                bar_dict.keys(),
                key=lambda s: bar_dict[s].priority + (0.5 if _is_on_strong_subdivision(s, slot.grid_size) else 0.0),
            )
            del bar_dict[worst_key]

    measures: list[ChartMeasure] = []
    for m_idx, slot in enumerate(slots):
        grid = [TaikoNote.REST.value] * slot.grid_size
        if m_idx in roll_measures:
            grid[0] = TaikoNote.DRUMROLL.value
            end_slot = max(1, slot.grid_size - 2)
            grid[end_slot] = TaikoNote.END.value
        else:
            for s_idx, cand in filtered_by_measure[m_idx].items():
                grid[s_idx] = int(cand.note_type)

        measures.append(
            ChartMeasure(
                measure_idx=m_idx,
                start_sec=slot.start_sec,
                end_sec=slot.end_sec,
                bpm=slot.bpm,
                numerator=slot.numerator,
                denominator=slot.denominator,
                scroll=1.0,
                notes=grid,
            )
        )

    return measures