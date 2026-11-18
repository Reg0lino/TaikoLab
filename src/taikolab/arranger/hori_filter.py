"""Hori ergonomics filter with source-supported rolls and Easy-mode phrase rests."""

from __future__ import annotations

from bisect import bisect_left

from taikolab.arranger.mapper_drums import PlacedCandidate
from taikolab.arranger.quantizer import MeasureSlot
from taikolab.arranger.section_builder import MeasurePlan
from taikolab.config import AppConfig
from taikolab.schemas import ChartMeasure, DifficultyLevel, TaikoNote

_MAX_FILL_ROLLS_PER_COURSE = 3
_MIN_ROLL_SPACING_MEASURES = 4


def _slot_time(slot: MeasureSlot, s_idx: int) -> float:
    dur = slot.end_sec - slot.start_sec
    return slot.start_sec + (s_idx / float(slot.grid_size)) * dur


def _is_on_strong_subdivision(s_idx: int, grid_size: int) -> bool:
    step = max(1, grid_size // 4)
    return (s_idx % step) == 0


def _find_fill_roll_span(
    bar_dict: dict[int, PlacedCandidate],
    slot: MeasureSlot,
    min_duration_sec: float,
) -> tuple[int, int] | None:
    min_hits = 5
    max_span_sec = min(1.2, (slot.end_sec - slot.start_sec) * 0.8)
    drum_events = sorted(
        (
            (s_idx, candidate)
            for s_idx, candidate in bar_dict.items()
            if candidate.source.startswith("drum:")
        ),
        key=lambda item: item[1].time_sec,
    )
    best: tuple[int, int, int, int] | None = None

    for start_idx in range(len(drum_events)):
        previous_source = ""
        source_run = 0
        longest_source_run = 0
        for end_idx in range(start_idx, len(drum_events)):
            start_time = drum_events[start_idx][1].time_sec
            end_time = drum_events[end_idx][1].time_sec
            if end_time - start_time > max_span_sec:
                break
            if end_idx > start_idx and end_time - drum_events[end_idx - 1][1].time_sec > 0.18:
                break

            source = drum_events[end_idx][1].source.partition(":")[2]
            source_run = source_run + 1 if source == previous_source else 1
            previous_source = source
            longest_source_run = max(longest_source_run, source_run)
            hit_count = end_idx - start_idx + 1
            span = end_time - start_time
            is_fill = longest_source_run >= 3
            if hit_count < min_hits or span < min_duration_sec or not is_fill:
                continue

            proposal = (hit_count, end_idx - start_idx, drum_events[start_idx][0], drum_events[end_idx][0])
            if best is None or proposal[:2] > best[:2]:
                best = proposal

    if best is None:
        return None
    return best[2], best[3]


def apply_hori_ergonomics(
    candidates: dict[tuple[int, int], PlacedCandidate],
    slots: list[MeasureSlot],
    plans: list[MeasurePlan],
    cfg: AppConfig,
    difficulty: DifficultyLevel,
) -> list[ChartMeasure]:
    """Apply Hori limits, source-supported fill rolls, and Easy-mode phrase rests."""
    by_measure: list[dict[int, PlacedCandidate]] = [{} for _ in slots]
    for (m_idx, s_idx), cand in candidates.items():
        if 0 <= m_idx < len(slots):
            by_measure[m_idx][s_idx] = cand

    roll_measures: set[int] = set()
    # Only fold into drumrolls if explicitly in chaos_roll or extreme powerviolence blast-zone
    for m_idx, (slot, plan) in enumerate(zip(slots, plans)):
        if plan.follow_mode == "chaos_roll" or (plan.is_blast_zone and plan.fold_blasts_to_rolls and plan.max_nps >= 8.0):
            bar_dur = slot.end_sec - slot.start_sec
            if bar_dur >= cfg.hori.min_roll_duration_sec * 0.8 and len(by_measure[m_idx]) >= 4:
                roll_measures.add(m_idx)

    if difficulty == "easy":
        active_streak = 0
        for m_idx in range(len(slots)):
            if len(by_measure[m_idx]) > 0:
                active_streak += 1
                if active_streak > 2:
                    by_measure[m_idx] = {}
                    active_streak = 0
            else:
                active_streak = 0

    roll_candidates: list[tuple[int, tuple[int, int]]] = []
    if difficulty in ("easy", "normal"):
        for m_idx, (slot, bar_dict) in enumerate(zip(slots, by_measure)):
            if m_idx in roll_measures or not bar_dict:
                continue
            span = _find_fill_roll_span(
                bar_dict,
                slot,
                cfg.hori.min_roll_duration_sec,
            )
            if span is not None:
                roll_candidates.append((m_idx, span))

    spaced_rolls: list[tuple[int, tuple[int, int]]] = []
    for candidate in roll_candidates:
        if not spaced_rolls or candidate[0] - spaced_rolls[-1][0] >= _MIN_ROLL_SPACING_MEASURES:
            spaced_rolls.append(candidate)
    if len(spaced_rolls) > _MAX_FILL_ROLLS_PER_COURSE:
        last_idx = len(spaced_rolls) - 1
        selected_indices = [
            round(i * last_idx / (_MAX_FILL_ROLLS_PER_COURSE - 1))
            for i in range(_MAX_FILL_ROLLS_PER_COURSE)
        ]
        spaced_rolls = [spaced_rolls[i] for i in selected_indices]

    fill_roll_spans = dict(spaced_rolls)
    for m_idx, (start_slot, end_slot) in fill_roll_spans.items():
        for s_idx in range(start_slot, end_slot + 1):
            by_measure[m_idx].pop(s_idx, None)

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
            if m_idx in fill_roll_spans:
                start_slot, end_slot = fill_roll_spans[m_idx]
                grid[start_slot] = TaikoNote.DRUMROLL.value
                grid[end_slot] = TaikoNote.END.value

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