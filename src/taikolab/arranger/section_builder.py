"""Automatic section classification, phrase alternation, and user override resolution."""

from __future__ import annotations

from dataclasses import dataclass

from taikolab.arranger.quantizer import MeasureSlot
from taikolab.config import AppConfig
from taikolab.schemas import (
    BlendStyle,
    DifficultyLevel,
    FollowMode,
    PhrasingLead,
    SectionDifficultyRule,
    SectionOverride,
    SongAnalysis,
    SongOverrides,
)


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
    ride_cymbal_mode: str = "accents_and_solo"
    cymbal_sensitivity: float = 1.0
    gp_accent: bool = False
    gp_tremolo: bool = False
    scroll_multiplier: float = 1.0
    density_scale: float = 1.0
    groove_triple_max_hits: int = 6
    invert_colors: bool = False
    blend_style: BlendStyle = "simultaneous"
    phrasing_lead: PhrasingLead = "auto"


def _default_mode_for_difficulty(difficulty: DifficultyLevel, cfg: AppConfig) -> FollowMode:
    if difficulty in ("easy", "normal"):
        return cfg.preset.normal_follow_mode
    if difficulty == "hard":
        return cfg.preset.hard_follow_mode
    return cfg.preset.oni_follow_mode


def resolve_follow_mode_at_time(
    time_sec: float,
    plan: MeasurePlan,
    overrides: SongOverrides | None,
) -> FollowMode:
    """Resolve section follow_mode at exact hit timestamp (supports mid-measure section splits & alternating phrases)."""
    if plan.blend_style != "simultaneous":
        return plan.follow_mode
    ov = _find_section_override(time_sec, overrides)
    if ov is None:
        return plan.follow_mode
    rule = ov.difficulty_rules.get(plan.difficulty)
    mode = rule.follow_mode if rule is not None else ov.follow_mode
    return mode if mode != "auto" else plan.follow_mode


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
    base_triple_max_hits = (
        overrides.groove_triple_max_hits
        if overrides and overrides.groove_triple_max_hits is not None
        else 6
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
        base_big = (
            overrides.enable_big_notes
            if overrides and overrides.enable_big_notes is not None
            else cfg.preset.enable_big_notes
        )
        enable_big = base_big and (difficulty != "easy")
        fold_blasts = (
            overrides.fold_blasts_to_rolls
            if overrides and overrides.fold_blasts_to_rolls is not None
            else cfg.preset.fold_blasts_to_rolls
        )
        max_nps = base_max_nps
        sec_scroll_mult = 1.0
        sec_density_scale = 1.0
        sec_triple_density = groove_triple_density
        sec_triple_max_hits = base_triple_max_hits
        sec_invert_colors = False
        sec_blend: BlendStyle = "simultaneous"
        sec_lead: PhrasingLead = "auto"

        if ov is not None:
            diff_rule = ov.difficulty_rules.get(difficulty)
            rule_obj = diff_rule if diff_rule is not None else ov
            if rule_obj.follow_mode != "auto":
                mode = rule_obj.follow_mode
            drum_sens = rule_obj.drum_sensitivity
            vocal_sens = rule_obj.vocal_sensitivity
            enable_big = rule_obj.enable_big_notes
            if rule_obj.fold_blasts_to_rolls is not None:
                fold_blasts = rule_obj.fold_blasts_to_rolls
            if rule_obj.max_nps is not None:
                max_nps = min(base_max_nps, rule_obj.max_nps) if difficulty == "easy" else rule_obj.max_nps
            sec_scroll_mult = rule_obj.scroll_multiplier
            sec_density_scale = rule_obj.density_scale
            if abs(sec_density_scale - 1.0) > 1e-4:
                max_nps = max(0.5, max_nps * sec_density_scale)
            if rule_obj.groove_triple_density is not None:
                sec_triple_density = rule_obj.groove_triple_density
            if rule_obj.groove_triple_max_hits is not None:
                sec_triple_max_hits = rule_obj.groove_triple_max_hits
            sec_invert_colors = bool(rule_obj.invert_colors)
            sec_blend = getattr(rule_obj, "blend_style", "simultaneous")
            sec_lead = getattr(rule_obj, "phrasing_lead", "auto")

        if sec_blend == "simultaneous" and overrides and overrides.default_blend_style:
            sec_blend = overrides.default_blend_style
        if sec_lead == "auto" and overrides and overrides.default_phrasing_lead:
            sec_lead = overrides.default_phrasing_lead

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
                groove_triple_density=sec_triple_density,
                max_groove_triples=max_groove_triples,
                groove_triple_complexity=groove_triple_complexity,
                groove_triple_ramp=groove_triple_ramp,
                groove_triple_max_bpm=groove_triple_max_bpm,
                ride_cymbal_mode=(overrides.ride_cymbal_mode if overrides and overrides.ride_cymbal_mode else "accents_and_solo"),
                cymbal_sensitivity=(overrides.cymbal_sensitivity if overrides and overrides.cymbal_sensitivity is not None else 1.0),
                gp_accent=bool(analysis.gp_hints and s.measure_idx in analysis.gp_hints.accent_bars),
                gp_tremolo=bool(analysis.gp_hints and s.measure_idx in analysis.gp_hints.tremolo_bars),
                scroll_multiplier=sec_scroll_mult,
                density_scale=sec_density_scale,
                groove_triple_max_hits=sec_triple_max_hits,
                invert_colors=sec_invert_colors,
                blend_style=sec_blend,
                phrasing_lead=sec_lead,
            )
        )

    _apply_phrase_alternation(plans, slots, analysis, difficulty, overrides)

    variety = (
        overrides.section_phrase_variety
        if overrides and overrides.section_phrase_variety is not None
        else 0.35
    )
    _apply_section_phrase_variation(plans, slots, analysis, difficulty, overrides, variety)
    return plans


def _apply_phrase_alternation(
    plans: list[MeasurePlan],
    slots: list[MeasureSlot],
    analysis: SongAnalysis,
    difficulty: DifficultyLevel,
    overrides: SongOverrides | None,
) -> None:
    """Intelligently alternate focus (e.g. 2-bar/4-bar vocal lead <-> drum groove) in blended sections."""
    if not plans or not slots:
        return

    sections = overrides.sections if overrides and overrides.sections else []
    ranges: list[tuple[int, int, SectionOverride | None]] = []
    if sections:
        for sec in sections:
            idxs = [
                i for i, s in enumerate(slots)
                if sec.start_sec <= 0.5 * (s.start_sec + s.end_sec) < sec.end_sec
            ]
            if idxs:
                ranges.append((idxs[0], idxs[-1] + 1, sec))
    else:
        ranges = [(0, len(slots), None)]

    for r_start, r_end, sec_ov in ranges:
        if r_end <= r_start:
            continue

        rule_obj = None
        if sec_ov is not None:
            rule_obj = sec_ov.difficulty_rules.get(difficulty) or sec_ov

        sec_blend = getattr(rule_obj, "blend_style", "simultaneous") if rule_obj else "simultaneous"
        if sec_blend == "simultaneous" and overrides and overrides.default_blend_style:
            sec_blend = overrides.default_blend_style

        if sec_blend == "simultaneous":
            continue

        is_selective = (
            overrides.selective_phrase_parts
            if overrides and overrides.selective_phrase_parts is not None
            else False
        )
        if is_selective and sec_ov is not None and (rule_obj is None or getattr(rule_obj, "blend_style", "simultaneous") == "simultaneous"):
            lbl = (sec_ov.label or "").lower()
            if any(tag in lbl for tag in ("chorus", "drive", "outro", "climax")):
                continue

        base_mode = plans[r_start].follow_mode
        if base_mode not in ("drums_vocals", "hybrid", "vocals_guitar", "all_balanced"):
            continue

        if base_mode == "drums_vocals":
            voice_a, voice_b = "vocals", "drums"
        elif base_mode == "hybrid":
            voice_a, voice_b = "guitar", "drums"
        elif base_mode == "vocals_guitar":
            voice_a, voice_b = "vocals", "guitar"
        else:
            voice_a, voice_b = "vocals", "drums"

        lead_pref = getattr(rule_obj, "phrasing_lead", "auto") if rule_obj else "auto"
        if lead_pref == "auto" and overrides and overrides.default_phrasing_lead:
            lead_pref = overrides.default_phrasing_lead

        lead_voice = voice_a
        response_voice = voice_b

        if lead_pref == "vocals":
            lead_voice = "vocals"
            response_voice = "drums" if voice_b == "drums" else "guitar"
        elif lead_pref == "drums":
            lead_voice = "drums"
            response_voice = "vocals" if voice_a == "vocals" else "guitar"
        elif lead_pref == "instruments":
            lead_voice = "guitar"
            response_voice = "drums" if voice_b == "drums" else "vocals"
        else:
            first_2_end = slots[min(r_end - 1, r_start + 1)].end_sec
            first_start = slots[r_start].start_sec
            if voice_a == "vocals":
                v_open = sum(1 for v in analysis.vocal_events if first_start <= v.time_sec < first_2_end)
                if v_open == 0:
                    lead_voice, response_voice = voice_b, voice_a
            elif voice_a == "guitar":
                g_open = sum(1 for g in analysis.guitar_events if first_start <= g.time_sec < first_2_end)
                if g_open == 0:
                    lead_voice, response_voice = voice_b, voice_a

        for m_pos, m_idx in enumerate(range(r_start, r_end)):
            plan = plans[m_idx]
            slot = slots[m_idx]
            plan.blend_style = sec_blend
            plan.phrasing_lead = lead_pref

            if sec_blend == "alt_2bar":
                block_idx = m_pos // 2
                target_voice = lead_voice if (block_idx % 2 == 0) else response_voice
            elif sec_blend == "alt_4bar":
                block_idx = m_pos // 4
                target_voice = lead_voice if (block_idx % 2 == 0) else response_voice
            elif sec_blend == "adaptive":
                if lead_voice == "vocals":
                    v_bar = sum(1 for v in analysis.vocal_events if slot.start_sec <= v.time_sec < slot.end_sec)
                    target_voice = lead_voice if v_bar > 0 else response_voice
                elif lead_voice == "guitar":
                    g_bar = sum(1 for g in analysis.guitar_events if slot.start_sec <= g.time_sec < slot.end_sec)
                    target_voice = lead_voice if g_bar > 0 else response_voice
                else:
                    d_bar = sum(1 for d in analysis.drum_hits if slot.start_sec <= d.time_sec < slot.end_sec and d.drum_class not in ("hihat", "ride"))
                    target_voice = lead_voice if d_bar > 0 else response_voice
            else:
                target_voice = base_mode

            # Strict guardrail with natural breathing space:
            other_voice = response_voice if target_voice == lead_voice else lead_voice
            if target_voice == "vocals":
                v_count = sum(1 for v in analysis.vocal_events if slot.start_sec <= v.time_sec < slot.end_sec)
                if v_count >= 1:
                    plan.follow_mode = "vocals"
                else:
                    plan.follow_mode = other_voice
            elif target_voice == "guitar":
                g_count = sum(1 for g in analysis.guitar_events if slot.start_sec <= g.time_sec < slot.end_sec)
                if g_count >= 1:
                    plan.follow_mode = "guitar"
                else:
                    plan.follow_mode = other_voice
            else:
                plan.follow_mode = target_voice


def _rules_for_section_role(role: str, has_vocals: bool, has_drums: bool) -> tuple[FollowMode, dict[DifficultyLevel, SectionDifficultyRule]]:
    if role == "intro":
        return "auto", {
            "easy": SectionDifficultyRule(follow_mode="guitar" if not has_drums else "drums"),
            "normal": SectionDifficultyRule(follow_mode="guitar" if not has_drums else "drums"),
            "hard": SectionDifficultyRule(follow_mode="guitar" if not has_drums else "hybrid"),
            "oni": SectionDifficultyRule(follow_mode="guitar" if not has_drums else "hybrid"),
        }
    if role == "verse":
        return "auto", {
            "easy": SectionDifficultyRule(follow_mode="vocals" if has_vocals else "drums"),
            "normal": SectionDifficultyRule(follow_mode="vocals" if has_vocals else "drums"),
            "hard": SectionDifficultyRule(follow_mode="drums_vocals" if (has_vocals and has_drums) else ("drums" if has_drums else "guitar")),
            "oni": SectionDifficultyRule(follow_mode="drums_vocals" if (has_vocals and has_drums) else "all_balanced"),
        }
    if role == "pre_chorus":
        return "auto", {
            "easy": SectionDifficultyRule(follow_mode="vocals" if has_vocals else "drums"),
            "normal": SectionDifficultyRule(follow_mode="drums_vocals" if has_vocals else "drums"),
            "hard": SectionDifficultyRule(follow_mode="drums_vocals" if has_vocals else "hybrid"),
            "oni": SectionDifficultyRule(follow_mode="all_balanced"),
        }
    if role == "chorus":
        return "auto", {
            "easy": SectionDifficultyRule(follow_mode="vocals" if has_vocals else "drums"),
            "normal": SectionDifficultyRule(follow_mode="drums_vocals" if has_vocals else "drums"),
            "hard": SectionDifficultyRule(follow_mode="all_balanced"),
            "oni": SectionDifficultyRule(follow_mode="all_balanced"),
        }
    if role in ("bridge", "interlude"):
        return "auto", {
            "easy": SectionDifficultyRule(follow_mode="vocals" if has_vocals else "guitar"),
            "normal": SectionDifficultyRule(follow_mode="vocals_guitar" if has_vocals else "hybrid"),
            "hard": SectionDifficultyRule(follow_mode="hybrid" if has_drums else "guitar"),
            "oni": SectionDifficultyRule(follow_mode="all_balanced" if has_drums else "vocals_guitar"),
        }
    if role == "solo":
        return "auto", {
            "easy": SectionDifficultyRule(follow_mode="guitar" if not has_drums else "drums"),
            "normal": SectionDifficultyRule(follow_mode="guitar"),
            "hard": SectionDifficultyRule(follow_mode="hybrid" if has_drums else "guitar"),
            "oni": SectionDifficultyRule(follow_mode="all_balanced" if has_drums else "guitar"),
        }
    return "auto", {}


def auto_build_sections(analysis: SongAnalysis, slots: list[MeasureSlot]) -> list[SectionOverride]:
    """Build bar-aligned musical sections from GP markers when present, else energy/stem transitions."""
    if not slots:
        return []

    def _bar_stats(slot: MeasureSlot):
        cores = sum(1 for h in analysis.drum_hits if slot.start_sec <= h.time_sec < slot.end_sec and h.drum_class not in ("hihat", "ride"))
        vocals = sum(1 for v in analysis.vocal_events if slot.start_sec <= v.time_sec < slot.end_sec)
        guitars = sum(1 for g in analysis.guitar_events if slot.start_sec <= g.time_sec < slot.end_sec)
        pts = [max(p.drums_rms, p.vocals_rms, p.guitar_rms) for p in analysis.energy_curve if slot.start_sec <= p.time_sec < slot.end_sec]
        energy = (sum(pts) / len(pts)) if pts else (0.7 if cores >= 3 else 0.25)
        return cores, vocals, guitars, energy

    stats = [_bar_stats(s) for s in slots]
    gp_sections = list(analysis.gp_hints.sections) if analysis.gp_hints and analysis.gp_hints.sections else []

    boundaries: list[tuple[int, str, str]] = []
    if gp_sections:
        seen_idx: set[int] = set()
        for gm in gp_sections:
            if gm.time_sec is not None:
                b_idx = min(range(len(slots)), key=lambda i: abs(slots[i].start_sec - gm.time_sec))
            else:
                b_idx = max(0, min(len(slots) - 1, gm.bar_idx))
            if b_idx not in seen_idx:
                seen_idx.add(b_idx)
                boundaries.append((b_idx, gm.label, gm.role))
        boundaries.sort(key=lambda item: item[0])
        if boundaries and boundaries[0][0] > 0:
            boundaries.insert(0, (0, "Intro", "intro"))
    else:
        b_indices = [0]
        last_b = 0
        for i in range(1, len(slots)):
            prev_c, prev_v, _, prev_e = stats[i - 1]
            cur_c, cur_v, _, cur_e = stats[i]
            bars_since = i - last_b
            tempo_jump = abs(slots[i].bpm - slots[i - 1].bpm) >= 8.0 or (
                slots[i].numerator != slots[i - 1].numerator or slots[i].denominator != slots[i - 1].denominator
            )
            intro_drop = prev_c <= 1 and cur_c >= 3 and bars_since >= 2
            phrase_b = (i % 4 == 0) and bars_since >= 4
            e_shift = abs(cur_e - prev_e) >= 0.22
            v_shift = (prev_v >= 2) != (cur_v >= 2)
            if tempo_jump or intro_drop or (phrase_b and (e_shift or v_shift or bars_since >= 16)):
                b_indices.append(i)
                last_b = i
        for pos, bi in enumerate(b_indices):
            end_i = b_indices[pos + 1] if pos + 1 < len(b_indices) else len(slots)
            c_sum = sum(stats[k][0] for k in range(bi, end_i))
            v_sum = sum(stats[k][1] for k in range(bi, end_i))
            n_b = max(1, end_i - bi)
            if pos == 0 and c_sum <= n_b:
                role, tag = "intro", "Intro"
            elif v_sum >= n_b * 1.5 and c_sum >= n_b * 2:
                role, tag = "verse", "Verse / Vocals"
            elif c_sum <= n_b and v_sum >= n_b:
                role, tag = "bridge", "Vocal Break"
            elif c_sum >= n_b * 3:
                role, tag = "chorus", "Drive / Riff"
            else:
                role, tag = "interlude", "Instrumental"
            boundaries.append((bi, f"Part {pos + 1} — {tag}", role))

    out: list[SectionOverride] = []
    for idx, (b_start, label, role) in enumerate(boundaries):
        b_end = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(slots)
        if b_end <= b_start:
            continue
        seg_start = 0.0 if idx == 0 else round(slots[b_start].start_sec, 3)
        seg_end = round(analysis.duration_sec, 3) if idx == len(boundaries) - 1 else round(slots[b_end].start_sec, 3)
        if seg_end <= seg_start + 0.05:
            continue
        has_v = sum(stats[k][1] for k in range(b_start, b_end)) >= max(2, (b_end - b_start))
        has_d = sum(stats[k][0] for k in range(b_start, b_end)) >= max(2, (b_end - b_start))
        base_mode, diff_rules = _rules_for_section_role(role, has_v, has_d)
        out.append(
            SectionOverride(
                start_sec=seg_start,
                end_sec=seg_end,
                label=label,
                follow_mode=base_mode,
                difficulty_rules=diff_rules,
            )
        )
    return out


def _apply_section_phrase_variation(
    plans: list[MeasurePlan],
    slots: list[MeasureSlot],
    analysis: SongAnalysis,
    difficulty: DifficultyLevel,
    overrides: SongOverrides | None,
    variety: float,
) -> None:
    """Procedurally rotate 2-bar/4-bar blocks (drums/vocals/guitar) inside long sections when active."""
    if variety <= 0.01 or difficulty == "easy" or not slots:
        return

    sections = overrides.sections if overrides and overrides.sections else []
    ranges: list[tuple[int, int, str]] = []
    if sections:
        for sec in sections:
            idxs = [
                i for i, s in enumerate(slots)
                if sec.start_sec <= 0.5 * (s.start_sec + s.end_sec) < sec.end_sec
            ]
            if idxs:
                ranges.append((idxs[0], idxs[-1] + 1, sec.label.lower()))
    else:
        ranges = [(0, len(slots), "song")]

    block_len = 2 if variety >= 0.65 else 4
    for r_start, r_end, label in ranges:
        if (r_end - r_start) < block_len * 2:
            continue
        if any(k in label for k in ("intro", "outro")):
            continue

        for block_idx, b0 in enumerate(range(r_start, r_end, block_len)):
            b1 = min(r_end, b0 + block_len)
            if (b1 - b0) < 2:
                continue
            if variety < 0.50 and (block_idx % 2 == 0):
                continue
            if block_idx == 0:
                continue

            v_count = sum(
                1 for v in analysis.vocal_events
                if slots[b0].start_sec <= v.time_sec < slots[b1 - 1].end_sec
            )
            g_count = sum(
                1 for g in analysis.guitar_events
                if slots[b0].start_sec <= g.time_sec < slots[b1 - 1].end_sec
            )
            d_count = sum(
                1 for h in analysis.drum_hits
                if slots[b0].start_sec <= h.time_sec < slots[b1 - 1].end_sec
                and h.drum_class not in ("hihat", "ride")
            )
            n_bars = b1 - b0
            if any(plans[k].low_energy_guitar for k in range(b0, b1)):
                continue
            if any(plans[k].blend_style != "simultaneous" for k in range(b0, b1)):
                continue

            cur_mode = plans[b0].follow_mode
            chosen: FollowMode | None = None
            if (block_idx % 3 == 1) and v_count >= n_bars * 2 and cur_mode != "vocals":
                chosen = "vocals" if difficulty == "normal" else "drums_vocals"
            elif (block_idx % 3 == 2) and g_count >= n_bars * 2 and cur_mode != "guitar":
                chosen = "guitar" if (variety >= 0.70 or d_count < n_bars * 2) else "hybrid"
            elif d_count >= n_bars * 2 and cur_mode not in ("drums", "all_balanced"):
                chosen = "drums"

            if chosen is not None:
                for k in range(b0, b1):
                    plans[k].follow_mode = chosen
