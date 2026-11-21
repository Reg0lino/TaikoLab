"""Vocal cadence & hook-driven mapping with balanced drum pocket support."""

from __future__ import annotations

from taikolab.arranger.mapper_drums import PlacedCandidate
from taikolab.arranger.quantizer import MeasureSlot, time_to_measure_slot
from taikolab.arranger.section_builder import MeasurePlan
from taikolab.config import AppConfig
from taikolab.schemas import DrumHit, OnsetEvent, TaikoNote


def map_vocal_cadence(
    vocal_events: list[OnsetEvent],
    drum_hits: list[DrumHit],
    slots: list[MeasureSlot],
    plans: list[MeasurePlan],
    cfg: AppConfig,
) -> dict[tuple[int, int], PlacedCandidate]:
    """Map vocal phrasing with natural Don/Kat cadence + kick/snare pocket anchors."""
    placed: dict[tuple[int, int], PlacedCandidate] = {}

    phrase_toggle = False
    last_time = -10.0

    for ev in sorted(vocal_events, key=lambda e: e.time_sec):
        loc = time_to_measure_slot(ev.time_sec, slots)
        if loc is None:
            continue
        m_idx, s_idx = loc
        plan = plans[m_idx]
        if plan.follow_mode != "vocals":
            continue
        if plan.low_energy_guitar:
            continue

        eff_str = min(1.0, ev.strength * plan.vocal_sensitivity)
        if eff_str < cfg.preset.vocal_min_strength:
            continue

        if ev.time_sec - last_time > 0.6:
            phrase_toggle = False
        else:
            phrase_toggle = not phrase_toggle
        last_time = ev.time_sec

        if plan.enable_big_notes and eff_str >= cfg.preset.big_note_threshold:
            note = TaikoNote.BIG_KAT
        else:
            note = TaikoNote.KAT if phrase_toggle else TaikoNote.DON

        key = (m_idx, s_idx)
        if key not in placed or eff_str > placed[key].priority:
            placed[key] = PlacedCandidate(
                measure_idx=m_idx,
                slot_idx=s_idx,
                time_sec=ev.time_sec,
                note_type=note,
                priority=eff_str,
                source="vocal",
            )

    for hit in drum_hits:
        if hit.drum_class not in ("kick", "snare", "crash") or hit.velocity < 0.62:
            continue
        loc = time_to_measure_slot(hit.time_sec, slots)
        if loc is None:
            continue
        m_idx, s_idx = loc
        if plans[m_idx].follow_mode != "vocals":
            continue
        key = (m_idx, s_idx)
        if key not in placed and s_idx % 4 == 0:
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
        if plan.follow_mode != "hybrid" or plan.low_energy_guitar:
            continue

        eff_str = min(1.0, ev.strength * plan.vocal_sensitivity)
        if eff_str < max(0.68, cfg.preset.vocal_min_strength + 0.25):
            continue

        key = (m_idx, s_idx)
        if key in merged:
            continue

        if any(abs(dt - ev.time_sec) < 0.18 for dt in drum_times):
            continue

        bar_hits = sum(1 for (bm, _) in merged if bm == m_idx)
        if bar_hits >= 5:
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