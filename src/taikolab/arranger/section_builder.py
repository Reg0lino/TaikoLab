"""Automatic section classification and user override resolution."""

from __future__ import annotations

from dataclasses import dataclass

from taikolab.arranger.quantizer import MeasureSlot
from taikolab.config import AppConfig
from taikolab.schemas import DifficultyLevel, FollowMode, SectionOverride, SongAnalysis, SongOverrides


@dataclass
class MeasurePlan:
    measure_idx: int
    follow_mode: FollowMode
    difficulty: DifficultyLevel = "hard"
    drum_sensitivity: float = 1.0
    vocal_sensitivity: float = 1.0
    enable_big_notes: bool = True
    fold_blasts_to_rolls: bool = True
    max_nps: float = 10.0
    min_gap_ms: float = 55.0
    is_blast_zone: bool = False
    low_energy_guitar: bool = False
    quiet_guitar_behavior: str = "downbeat"
    max_fill_rolls: int = 0
    groove_triple_density: float | None = None
    max_groove_triples: int | None = None
    groove_triple_complexity: float = 0.25
    groove_triple_ramp: float = 0.50
    groove_triple_max_bpm: float = 235.0


def _default_mode_for_difficulty(difficulty: DifficultyLevel, cfg: AppConfig) -> FollowMode:
    if difficulty in ("easy", "normal"):
        return cfg.preset.normal_follow_mode
    if difficulty == "hard":
        return cfg.preset.hard_follow_mode
    return cfg.preset.oni_follow_mode


def _find_section_override(time_sec: float, overrides: SongOverrides | None) -> SectionOverride | None:
    if not overrides:
        return None
    for sec in overrides.sections:
        if sec.start_sec <= time_sec < sec.end_sec:
            return sec
    return None


def build_measure_plans(
    slots: list[MeasureSlot],
    analysis: SongAnalysis,
    difficulty: DifficultyLevel,
    cfg: AppConfig,
    overrides: SongOverrides | None = None,
) -> list[MeasurePlan]:
    """Decide per measure whether to follow drums, vocals, guitar, hybrid, or chaos_roll."""
    base_mode = _default_mode_for_difficulty(difficulty, cfg)
    base_max_nps = cfg.hori.max_nps_for(difficulty)
    base_min_gap_ms = cfg.hori.min_gap_ms_for(difficulty)

    if overrides and difficulty in overrides.difficulty_overrides:
        diff_ov = overrides.difficulty_overrides[difficulty]
        if diff_ov.follow_mode is not None:
            base_mode = diff_ov.follow_mode
        if diff_ov.max_nps is not None:
            base_max_nps = diff_ov.max_nps
        if diff_ov.density_scale is not None:
            base_max_nps *= diff_ov.density_scale

    max_fill_rolls = 3 if difficulty in ("easy", "normal") else 0
    if overrides and difficulty in overrides.fill_rolls_by_course:
        max_fill_rolls = overrides.fill_rolls_by_course[difficulty]
    elif overrides and overrides.fill_rolls_per_course is not None:
        max_fill_rolls = overrides.fill_rolls_per_course

    default_triple_density = {"easy": 0.0, "normal": 0.15, "hard": 0.45, "oni": 0.70, "ura": 0.75}.get(difficulty, 0.0)
    groove_triple_density: float | None = None
    max_groove_triples: int | None = None

    if overrides and difficulty in overrides.groove_triple_density_by_course:
        groove_triple_density = overrides.groove_triple_density_by_course[difficulty]
    elif overrides and difficulty in overrides.groove_triples_by_course:
        max_groove_triples = overrides.groove_triples_by_course[difficulty]
    else:
        groove_triple_density = default_triple_density

    if overrides and difficulty in overrides.difficulty_overrides:
        d_scale = overrides.difficulty_overrides[difficulty].density_scale
        if d_scale is not None and d_scale < 1.0:
            if groove_triple_density is not None:
                groove_triple_density = groove_triple_density * d_scale
            if max_groove_triples is not None:
                max_groove_triples = int(round(max_groove_triples * d_scale))
    default_triple_comp = {"easy": 0.0, "normal": 0.15, "hard": 0.25, "oni": 0.80, "ura": 0.85}.get(difficulty, 0.35)
    if overrides and difficulty in overrides.groove_triple_complexity_by_course:
        groove_triple_complexity = overrides.groove_triple_complexity_by_course[difficulty]
    elif overrides and overrides.groove_triple_complexity is not None:
        groove_triple_complexity = overrides.groove_triple_complexity
    else:
        groove_triple_complexity = default_triple_comp

    groove_triple_ramp = (
        overrides.groove_triple_ramp
        if overrides and overrides.groove_triple_ramp is not None
        else 0.50
    )
    groove_triple_max_bpm = (
        overrides.groove_triple_max_bpm
        if overrides and overrides.groove_triple_max_bpm is not None
        else 235.0
    )
    quiet_guitar_threshold = cfg.settings.quiet_guitar_rms_threshold
    quiet_guitar_behavior = "downbeat"
    if overrides:
        if overrides.quiet_guitar_rms_threshold is not None:
            quiet_guitar_threshold = overrides.quiet_guitar_rms_threshold
        if overrides.quiet_guitar_behavior is not None:
            quiet_guitar_behavior = overrides.quiet_guitar_behavior

    plans: list[MeasurePlan] = []
    for s in slots:
        mid = 0.5 * (s.start_sec + s.end_sec)
        dur = max(0.05, s.end_sec - s.start_sec)

        bar_drums = [
            h for h in analysis.drum_hits
            if s.start_sec <= h.time_sec < s.end_sec and h.drum_class != "hihat"
        ]
        bar_cymbals = [
            h for h in analysis.drum_hits
            if s.start_sec <= h.time_sec < s.end_sec and h.drum_class in ("hihat", "ride") and h.velocity >= 0.40
        ]
        bar_vocals = [
            v for v in analysis.vocal_events
            if s.start_sec <= v.time_sec < s.end_sec
        ]
        bar_guitars = [
            g for g in analysis.guitar_events
            if s.start_sec <= g.time_sec < s.end_sec
        ]
        guitar_energy = [
            point.guitar_rms
            for point in analysis.energy_curve
            if s.start_sec <= point.time_sec < s.end_sec
        ]
        low_energy_guitar = (
            bool(bar_guitars)
            and not bar_drums
            and bool(guitar_energy)
            and any(value > 0.0 for value in guitar_energy)
            and sum(guitar_energy) / len(guitar_energy) < quiet_guitar_threshold
        )

        drum_nps = len(bar_drums) / dur
        is_blast = (s.bpm >= cfg.hori.blast_bpm_threshold and drum_nps >= max(15.2, base_max_nps * 1.20))

        mode = base_mode
        if mode == "auto":
            if len(bar_drums) <= 2 and len(bar_vocals) >= 3:
                mode = "vocals"
            elif difficulty in ("hard", "oni", "ura") and is_blast and cfg.preset.fold_blasts_to_rolls:
                mode = "chaos_roll"
            else:
                mode = "hybrid" if difficulty in ("oni", "ura") else "drums"

        has_timekeeping_cymbals = difficulty != "easy" and len(bar_cymbals) >= 2
        # Keep rhythms fluid: avoid dead bars when vocals or drums briefly drop out.
        if difficulty in ("easy", "normal") and mode == "vocals" and len(bar_vocals) < 2:
            if len(bar_drums) >= 2 or has_timekeeping_cymbals:
                mode = "drums"
            elif len(bar_guitars) >= 2 or low_energy_guitar:
                mode = "guitar"
        elif mode == "drums" and len(bar_drums) <= 2 and not has_timekeeping_cymbals:
            if len(bar_guitars) >= 2 or low_energy_guitar:
                mode = "hybrid" if len(bar_drums) > 0 else "guitar"
            elif len(bar_vocals) >= 2 and len(bar_drums) == 0:
                mode = "vocals"

        ov = _find_section_override(mid, overrides)
        drum_sens = 1.0
        vocal_sens = 1.0
        enable_big = cfg.preset.enable_big_notes and (difficulty != "easy")
        fold_blasts = cfg.preset.fold_blasts_to_rolls
        max_nps = base_max_nps

        if ov is not None:
            diff_rule = ov.difficulty_rules.get(difficulty)
            if diff_rule is not None:
                if diff_rule.follow_mode != "auto":
                    mode = diff_rule.follow_mode
                drum_sens = diff_rule.drum_sensitivity
                vocal_sens = diff_rule.vocal_sensitivity
                enable_big = diff_rule.enable_big_notes
                if diff_rule.fold_blasts_to_rolls is not None:
                    fold_blasts = diff_rule.fold_blasts_to_rolls
                if diff_rule.max_nps is not None:
                    max_nps = min(base_max_nps, diff_rule.max_nps) if difficulty == "easy" else diff_rule.max_nps
            else:
                if ov.follow_mode != "auto":
                    mode = ov.follow_mode
                drum_sens = ov.drum_sensitivity
                vocal_sens = ov.vocal_sensitivity
                enable_big = ov.enable_big_notes
                if ov.fold_blasts_to_rolls is not None:
                    fold_blasts = ov.fold_blasts_to_rolls
                if ov.max_nps is not None:
                    max_nps = min(base_max_nps, ov.max_nps) if difficulty == "easy" else ov.max_nps

        plans.append(
            MeasurePlan(
                measure_idx=s.measure_idx,
                follow_mode=mode,
                difficulty=difficulty,
                drum_sensitivity=drum_sens,
                vocal_sensitivity=vocal_sens,
                enable_big_notes=enable_big,
                fold_blasts_to_rolls=fold_blasts,
                max_nps=max_nps,
                min_gap_ms=base_min_gap_ms,
                is_blast_zone=is_blast,
                low_energy_guitar=low_energy_guitar and mode in ("guitar", "hybrid"),
                quiet_guitar_behavior=quiet_guitar_behavior,
                max_fill_rolls=max_fill_rolls,
                groove_triple_density=groove_triple_density,
                max_groove_triples=max_groove_triples,
                groove_triple_complexity=groove_triple_complexity,
                groove_triple_ramp=groove_triple_ramp,
                groove_triple_max_bpm=groove_triple_max_bpm,
            )
        )

    return plans