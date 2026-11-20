"""Star-rating estimation, per-section shift, manual note edits & multi-difficulty build."""

from __future__ import annotations

from dataclasses import replace
from math import lcm

from taikolab.arranger.hori_filter import apply_hori_ergonomics
from taikolab.arranger.mapper_drums import PlacedCandidate, map_drum_hits
from taikolab.arranger.mapper_fusion import apply_guitar_fusion
from taikolab.arranger.mapper_vocals import map_vocal_cadence
from taikolab.arranger.quantizer import MeasureSlot, time_to_measure_slot
from taikolab.arranger.section_builder import build_measure_plans
from taikolab.config import AppConfig
from taikolab.schemas import (
    ChartDifficulty,
    ChartMeasure,
    DifficultyLevel,
    DrumHit,
    OnsetEvent,
    SongAnalysis,
    SongOverrides,
    TaikoNote,
)


def estimate_star_rating(difficulty: DifficultyLevel, measures: list[ChartMeasure]) -> int:
    if not measures:
        return 1
    total_notes = sum(
        1 for m in measures for n in m.notes if n in (TaikoNote.DON, TaikoNote.KAT, TaikoNote.BIG_DON, TaikoNote.BIG_KAT)
    )
    dur = max(1.0, measures[-1].end_sec - measures[0].start_sec)
    avg_nps = total_notes / dur

    raw = int(round(avg_nps * 1.1))
    if difficulty == "easy":
        return max(1, min(4, raw))
    if difficulty == "normal":
        return max(3, min(6, raw + 1))
    if difficulty == "hard":
        return max(5, min(8, raw + 1))
    return max(7, min(10, raw + 1))


def _warp_time_between_slots(
    time_sec: float,
    base_slots: list[MeasureSlot],
    warped_slots: list[MeasureSlot],
) -> float:
    if not base_slots or len(base_slots) != len(warped_slots):
        return time_sec
    if time_sec <= base_slots[0].start_sec:
        return max(0.0, time_sec + (warped_slots[0].start_sec - base_slots[0].start_sec))
    for base, warped in zip(base_slots, warped_slots):
        if base.start_sec <= time_sec < base.end_sec:
            base_dur = max(1e-6, base.end_sec - base.start_sec)
            rel = (time_sec - base.start_sec) / base_dur
            return warped.start_sec + rel * (warped.end_sec - warped.start_sec)
    return max(0.0, time_sec + (warped_slots[-1].end_sec - base_slots[-1].end_sec))


def _slots_differ(base_slots: list[MeasureSlot], warped_slots: list[MeasureSlot]) -> bool:
    if len(base_slots) != len(warped_slots):
        return False
    return any(
        abs(a.start_sec - b.start_sec) > 1e-6 or abs(a.end_sec - b.end_sec) > 1e-6
        for a, b in zip(base_slots, warped_slots)
    )


def _repeated_drum_runs(
    candidates: dict[tuple[int, int], PlacedCandidate],
    measure_count: int,
    minimum_length: int = 4,
) -> list[list[int]]:
    signatures = []
    for measure_idx in range(measure_count):
        signatures.append(
            tuple(
                sorted(
                    (slot_idx, candidate.note_type, candidate.source)
                    for (candidate_measure, slot_idx), candidate in candidates.items()
                    if candidate_measure == measure_idx and candidate.source.startswith("drum:")
                )
            )
        )

    runs: list[list[int]] = []
    start = 0
    while start < len(signatures):
        end = start + 1
        while end < len(signatures) and signatures[end] and signatures[end] == signatures[start]:
            end += 1
        if signatures[start] and end - start >= minimum_length:
            runs.append(list(range(start, end)))
        start = end
    return runs


def _apply_repeated_phrase_color_variation(
    candidates: dict[tuple[int, int], PlacedCandidate],
    drum_candidates: dict[tuple[int, int], PlacedCandidate],
    measure_count: int,
    intensity: float,
) -> dict[tuple[int, int], PlacedCandidate]:
    if intensity <= 0.0:
        return candidates

    color_swaps = {
        TaikoNote.DON.value: TaikoNote.KAT.value,
        TaikoNote.KAT.value: TaikoNote.DON.value,
        TaikoNote.BIG_DON.value: TaikoNote.BIG_KAT.value,
        TaikoNote.BIG_KAT.value: TaikoNote.BIG_DON.value,
    }
    eligible: list[tuple[int, int]] = []
    for run in _repeated_drum_runs(drum_candidates, measure_count):
        for measure_idx in run[1:]:
            for key, original in drum_candidates.items():
                if (
                    key[0] != measure_idx
                    or key[1] == 0
                    or not original.source.startswith("drum:")
                ):
                    continue
                current = candidates.get(key)
                if (
                    current is not None
                    and current.source.startswith("drum:")
                    and current.note_type in color_swaps
                ):
                    eligible.append(key)

    if not eligible:
        return candidates

    selected_count = min(len(eligible), round(len(eligible) * min(1.0, intensity)))
    selected_count = max(1, selected_count)
    if selected_count == len(eligible):
        selected = eligible
    elif selected_count == 1:
        selected = [eligible[len(eligible) // 2]]
    else:
        selected = [
            eligible[round(index * (len(eligible) - 1) / (selected_count - 1))]
            for index in range(selected_count)
        ]

    varied = dict(candidates)
    for key in selected:
        candidate = varied[key]
        varied[key] = replace(candidate, note_type=color_swaps[candidate.note_type])
    return varied


def _add_repeated_phrase_ride_accents(
    candidates: dict[tuple[int, int], PlacedCandidate],
    drum_candidates: dict[tuple[int, int], PlacedCandidate],
    drum_hits: list[DrumHit],
    slots: list[MeasureSlot],
    difficulty: DifficultyLevel,
) -> dict[tuple[int, int], PlacedCandidate]:
    if difficulty not in ("hard", "oni"):
        return candidates

    repeated_runs = _repeated_drum_runs(drum_candidates, len(slots))
    if difficulty == "hard":
        late_runs = [run for run in repeated_runs if run[-1] + 1 >= 0.75 * len(slots)]
        repeated_runs = late_runs[-1:] if late_runs else []
    if not repeated_runs:
        return candidates

    ride_hits_by_slot: dict[tuple[int, int], DrumHit] = {}
    for hit in drum_hits:
        if hit.drum_class != "ride":
            continue
        location = time_to_measure_slot(hit.time_sec, slots)
        if location is None:
            continue
        key = location
        current = ride_hits_by_slot.get(key)
        if current is None or hit.velocity > current.velocity:
            ride_hits_by_slot[key] = hit

    varied = dict(candidates)
    hard_patterns = ((2, 10), (6, 14))
    oni_patterns = ((2, 10), (2, 6, 10, 14), (6, 14), (2, 6, 10, 14))
    patterns = hard_patterns if difficulty == "hard" else oni_patterns

    for run in repeated_runs:
        for repeat_idx, measure_idx in enumerate(run):
            slot = slots[measure_idx]
            if slot.numerator != 4 or slot.denominator != 4 or slot.grid_size != 16:
                continue
            selected_slots = patterns[repeat_idx % len(patterns)]
            for slot_idx in selected_slots:
                key = (measure_idx, slot_idx)
                hit = ride_hits_by_slot.get(key)
                if key in varied or hit is None:
                    continue
                varied[key] = PlacedCandidate(
                    measure_idx=measure_idx,
                    slot_idx=slot_idx,
                    time_sec=hit.time_sec,
                    note_type=TaikoNote.KAT,
                    priority=0.55,
                    source="drum:ride",
                )

    return varied


def build_course(
    analysis: SongAnalysis,
    slots: list[MeasureSlot],
    difficulty: DifficultyLevel,
    cfg: AppConfig,
    overrides: SongOverrides | None = None,
    base_slots: list[MeasureSlot] | None = None,
) -> ChartDifficulty:
    source_slots = base_slots if base_slots is not None else slots
    warped = _slots_differ(source_slots, slots)

    if warped:
        drum_hits = [
            DrumHit(
                time_sec=_warp_time_between_slots(h.time_sec, source_slots, slots),
                drum_class=h.drum_class,
                velocity=h.velocity,
                source=h.source,
            )
            for h in analysis.drum_hits
        ]
        vocal_events = [
            OnsetEvent(
                time_sec=_warp_time_between_slots(v.time_sec, source_slots, slots),
                strength=v.strength,
                duration_sec=v.duration_sec,
                pitch_hz=v.pitch_hz,
            )
            for v in analysis.vocal_events
        ]
        guitar_events = [
            OnsetEvent(
                time_sec=_warp_time_between_slots(g.time_sec, source_slots, slots),
                strength=g.strength,
                duration_sec=g.duration_sec,
            )
            for g in analysis.guitar_events
        ]
        energy_curve = [
            e.model_copy(update={"time_sec": _warp_time_between_slots(e.time_sec, source_slots, slots)})
            for e in analysis.energy_curve
        ]
        effective_analysis = analysis.model_copy(
            update={
                "drum_hits": drum_hits,
                "vocal_events": vocal_events,
                "guitar_events": guitar_events,
                "energy_curve": energy_curve,
            }
        )
    else:
        drum_hits = analysis.drum_hits
        vocal_events = analysis.vocal_events
        guitar_events = analysis.guitar_events
        effective_analysis = analysis

    plans = build_measure_plans(slots, effective_analysis, difficulty, cfg, overrides)

    drum_cands = map_drum_hits(drum_hits, slots, plans, cfg)
    vocal_cands = map_vocal_cadence(vocal_events, drum_hits, slots, plans, cfg)

    combined = dict(drum_cands)
    combined.update(vocal_cands)

    fused = apply_guitar_fusion(combined, guitar_events, slots, plans, cfg)
    variation_intensity = 0.0
    if overrides and difficulty in overrides.difficulty_overrides:
        variation_intensity = overrides.difficulty_overrides[difficulty].phrase_variation or 0.0
    fused = _apply_repeated_phrase_color_variation(
        fused,
        drum_cands,
        len(slots),
        variation_intensity,
    )
    fused = _add_repeated_phrase_ride_accents(fused, drum_cands, drum_hits, slots, difficulty)
    measures = apply_hori_ergonomics(fused, slots, plans, cfg, difficulty)

    if overrides and overrides.note_edits:
        prefix = f"{difficulty}:"
        valid_codes = {n.value for n in TaikoNote}
        base_lengths = [len(measure.notes) for measure in measures]
        base_edits: list[tuple[int, int, int]] = []
        positioned_edits: list[tuple[int, int, int, int]] = []
        for key, code in overrides.note_edits.items():
            if not key.startswith(prefix) or code not in valid_codes:
                continue
            parts = key.split(":")
            if len(parts) != 3:
                continue
            try:
                m_idx = int(parts[1])
            except ValueError:
                continue
            if not 0 <= m_idx < len(measures):
                continue
            position = parts[2]
            if position.startswith("p"):
                try:
                    numerator, denominator = map(int, position[1:].split("/", 1))
                except ValueError:
                    continue
                if denominator < 1 or denominator > 256 or not 0 <= numerator < denominator:
                    continue
                positioned_edits.append((m_idx, numerator, denominator, code))
            else:
                try:
                    s_idx = int(position)
                except ValueError:
                    continue
                if 0 <= s_idx < base_lengths[m_idx]:
                    base_edits.append((m_idx, s_idx, code))

        denominators_by_measure: dict[int, list[int]] = {}
        for m_idx, _, denominator, _ in positioned_edits:
            denominators_by_measure.setdefault(m_idx, []).append(denominator)

        for m_idx, denominators in denominators_by_measure.items():
            measure = measures[m_idx]
            base_length = base_lengths[m_idx]
            expanded_length = lcm(base_length, *denominators)
            if expanded_length > 4096:
                raise ValueError(f"Manual edit grid for measure {m_idx} exceeds 4096 slots")
            if expanded_length != base_length:
                expanded = [TaikoNote.REST.value] * expanded_length
                for slot_idx, note in enumerate(measure.notes):
                    expanded[slot_idx * (expanded_length // base_length)] = note
                measure.notes = expanded

        for m_idx, s_idx, code in base_edits:
            resolution = len(measures[m_idx].notes) // base_lengths[m_idx]
            measures[m_idx].notes[s_idx * resolution] = code

        for m_idx, numerator, denominator, code in positioned_edits:
            resolution = len(measures[m_idx].notes)
            if resolution % denominator == 0:
                measures[m_idx].notes[numerator * (resolution // denominator)] = code

    stars = estimate_star_rating(difficulty, measures)
    if overrides and difficulty in overrides.difficulty_overrides:
        ov_star = overrides.difficulty_overrides[difficulty].star_rating
        if ov_star is not None:
            stars = ov_star

    return ChartDifficulty(
        difficulty=difficulty,
        level_stars=stars,
        measures=measures,
    )