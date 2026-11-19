"""Vocal cadence & hook-driven mapping (ideal for Normal mode & vocal verses)."""

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
    """Map vocal phrasing with natural Don/Kat cadence + downbeat kick anchors."""
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
        if hit.drum_class not in ("kick", "snare", "crash") or hit.velocity < 0.72:
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
                priority=0.45 * hit.velocity,
                source=f"vocal_anchor:{hit.drum_class}",
            )

    return placed