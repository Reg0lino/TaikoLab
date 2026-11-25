"""Vocal cadence & hook-driven mapping with pitch-contour phrasing and sub-bar section lookup."""

from __future__ import annotations

from taikolab.arranger.mapper_drums import PlacedCandidate
from taikolab.arranger.quantizer import MeasureSlot, time_to_measure_slot
from taikolab.arranger.section_builder import MeasurePlan, resolve_follow_mode_at_time
from taikolab.config import AppConfig
from taikolab.schemas import DrumHit, OnsetEvent, SongOverrides, TaikoNote


def map_vocal_cadence(
    vocal_events: list[OnsetEvent],
    drum_hits: list[DrumHit],
    slots: list[MeasureSlot],
    plans: list[MeasurePlan],
    cfg: AppConfig,
    overrides: SongOverrides | None = None,
) -> dict[tuple[int, int], PlacedCandidate]:
    """Map vocal phrasing with melodic Don/Kat contour + sparse drum pocket anchors."""
    placed: dict[tuple[int, int], PlacedCandidate] = {}

    phrase_toggle = False
    last_time = -10.0
    last_pitch: float | None = None
    vocal_count_by_bar: dict[int, int] = {}

    for ev in sorted(vocal_events, key=lambda e: e.time_sec):
        loc = time_to_measure_slot(ev.time_sec, slots)
        if loc is None:
            continue
        m_idx, s_idx = loc
        plan = plans[m_idx]
        eff_mode = resolve_follow_mode_at_time(ev.time_sec, plan, overrides)
        if eff_mode not in ("vocals", "vocals_guitar"):
            continue
        if plan.low_energy_guitar:
            continue

        eff_str = min(1.0, ev.strength * plan.vocal_sensitivity)
        if eff_str < cfg.preset.vocal_min_strength:
            continue

        if ev.time_sec - last_time > 0.55:
            phrase_toggle = False
        elif ev.pitch_hz is not None and last_pitch is not None and abs(ev.pitch_hz - last_pitch) >= 12.0:
            phrase_toggle = ev.pitch_hz > last_pitch
        else:
            phrase_toggle = not phrase_toggle
        last_time = ev.time_sec
        if ev.pitch_hz is not None:
            last_pitch = ev.pitch_hz

        if plan.enable_big_notes and eff_str >= cfg.preset.big_note_threshold:
            note = TaikoNote.BIG_DON if s_idx == 0 else TaikoNote.BIG_KAT
        else:
            note = TaikoNote.KAT if phrase_toggle else TaikoNote.DON

        key = (m_idx, s_idx)
        if key not in placed or eff_str > placed[key].priority:
            if key not in placed:
                vocal_count_by_bar[m_idx] = vocal_count_by_bar.get(m_idx, 0) + 1
            placed[key] = PlacedCandidate(
                measure_idx=m_idx,
                slot_idx=s_idx,
                time_sec=ev.time_sec,
                note_type=note,
                priority=eff_str,
                source="vocal",
            )

    for hit in drum_hits:
        if hit.drum_class not in ("kick", "snare", "crash") or hit.velocity < 0.65:
            continue
        loc = time_to_measure_slot(hit.time_sec, slots)
        if loc is None:
            continue
        m_idx, s_idx = loc
        if resolve_follow_mode_at_time(hit.time_sec, plans[m_idx], overrides) != "vocals":
            continue
        if plans[m_idx].blend_style != "simultaneous" and vocal_count_by_bar.get(m_idx, 0) > 0:
            continue
        if vocal_count_by_bar.get(m_idx, 0) > 2:
            continue
        key = (m_idx, s_idx)
        beat_step = max(1, slots[m_idx].grid_size // max(1, slots[m_idx].numerator))
        if key not in placed and (s_idx % beat_step) == 0:
            note = TaikoNote.DON if hit.drum_class == "kick" else TaikoNote.KAT
            placed[key] = PlacedCandidate(
                measure_idx=m_idx,
                slot_idx=s_idx,
                time_sec=hit.time_sec,
                note_type=note,
                priority=0.48 * hit.velocity,
                source=f"vocal_anchor:{hit.drum_class}",
            )

    return placed


def weave_hybrid_vocal_hooks(
    placed: dict[tuple[int, int], PlacedCandidate],
    vocal_events: list[OnsetEvent],
    slots: list[MeasureSlot],
    plans: list[MeasurePlan],
    cfg: AppConfig,
    overrides: SongOverrides | None = None,
) -> dict[tuple[int, int], PlacedCandidate]:
    """Add standout vocal phrase entries into open drum pockets in hybrid mode."""
    merged = dict(placed)
    drum_times = sorted(c.time_sec for c in placed.values())

    for ev in sorted(vocal_events, key=lambda e: e.time_sec):
        loc = time_to_measure_slot(ev.time_sec, slots)
        if loc is None:
            continue
        m_idx, s_idx = loc
        plan = plans[m_idx]
        eff_mode = resolve_follow_mode_at_time(ev.time_sec, plan, overrides)
        if eff_mode not in ("hybrid", "drums_vocals", "all_balanced") or plan.low_energy_guitar:
            continue

        explicit_vocals = eff_mode in ("drums_vocals", "all_balanced")
        eff_str = min(1.0, ev.strength * plan.vocal_sensitivity)
        min_str = max(0.42, cfg.preset.vocal_min_strength) if explicit_vocals else max(0.68, cfg.preset.vocal_min_strength + 0.25)
        if eff_str < min_str:
            continue

        key = (m_idx, s_idx)
        if key in merged:
            continue

        clearance = 0.13 if explicit_vocals else 0.18
        if any(abs(dt - ev.time_sec) < clearance for dt in drum_times):
            continue

        bar_hits = sum(1 for (bm, _) in merged if bm == m_idx)
        max_bar = 8 if explicit_vocals else 5
        if bar_hits >= max_bar:
            continue

        merged[key] = PlacedCandidate(
            measure_idx=m_idx,
            slot_idx=s_idx,
            time_sec=ev.time_sec,
            note_type=TaikoNote.KAT if (s_idx % 4 != 0) else TaikoNote.DON,
            priority=0.52 * eff_str,
            source="vocal:hook",
        )
        drum_times.append(ev.time_sec)

    return merged
