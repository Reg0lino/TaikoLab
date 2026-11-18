"""Star-rating estimation, per-section shift, manual note edits & multi-difficulty build."""

from __future__ import annotations

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


def _section_shift_at(t: float, overrides: SongOverrides | None) -> float:
    if not overrides:
        return 0.0
    for sec in overrides.sections:
        if sec.start_sec <= t < sec.end_sec and abs(sec.offset_shift_ms) > 0.01:
            return sec.offset_shift_ms / 1000.0
    return 0.0


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
) -> ChartDifficulty:
    plans = build_measure_plans(slots, analysis, difficulty, cfg, overrides)

    has_shifts = bool(overrides and any(abs(s.offset_shift_ms) > 0.01 for s in overrides.sections))
    if has_shifts:
        drum_hits = [
            DrumHit(
                time_sec=max(0.0, h.time_sec + _section_shift_at(h.time_sec, overrides)),
                drum_class=h.drum_class,
                velocity=h.velocity,
                source=h.source,
            )
            for h in analysis.drum_hits
        ]
        vocal_events = [
            OnsetEvent(
                time_sec=max(0.0, v.time_sec + _section_shift_at(v.time_sec, overrides)),
                strength=v.strength,
                duration_sec=v.duration_sec,
                pitch_hz=v.pitch_hz,
            )
            for v in analysis.vocal_events
        ]
        guitar_events = [
            OnsetEvent(
                time_sec=max(0.0, g.time_sec + _section_shift_at(g.time_sec, overrides)),
                strength=g.strength,
                duration_sec=g.duration_sec,
            )
            for g in analysis.guitar_events
        ]
    else:
        drum_hits = analysis.drum_hits
        vocal_events = analysis.vocal_events
        guitar_events = analysis.guitar_events

    drum_cands = map_drum_hits(drum_hits, slots, plans, cfg)
    vocal_cands = map_vocal_cadence(vocal_events, drum_hits, slots, plans, cfg)

    combined = dict(drum_cands)
    combined.update(vocal_cands)

    fused = apply_guitar_fusion(combined, guitar_events, slots, plans, cfg)
    fused = _add_repeated_phrase_ride_accents(fused, drum_cands, drum_hits, slots, difficulty)
    measures = apply_hori_ergonomics(fused, slots, plans, cfg, difficulty)

    if overrides and overrides.note_edits:
        prefix = f"{difficulty}:"
        valid_codes = {n.value for n in TaikoNote}
        for key, code in overrides.note_edits.items():
            if not key.startswith(prefix) or code not in valid_codes:
                continue
            parts = key.split(":")
            if len(parts) != 3:
                continue
            try:
                m_idx = int(parts[1])
                s_idx = int(parts[2])
            except ValueError:
                continue
            if 0 <= m_idx < len(measures) and 0 <= s_idx < len(measures[m_idx].notes):
                measures[m_idx].notes[s_idx] = code

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