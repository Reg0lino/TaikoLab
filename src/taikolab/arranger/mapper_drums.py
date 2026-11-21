"""Drum-driven Taiko mapping (Kick=Don, Snare/HighTom=Kat, LowTom=Don, Fill Sticking)."""

from __future__ import annotations

from dataclasses import dataclass

from taikolab.arranger.quantizer import MeasureSlot, time_to_measure_slot
from taikolab.arranger.section_builder import MeasurePlan
from taikolab.config import AppConfig
from taikolab.schemas import DrumHit, TaikoNote, TaikoTarget

_HIGH_TOM_PITCHES = frozenset({47, 48, 50})
_LOW_TOM_PITCHES = frozenset({41, 43, 45})


@dataclass
class PlacedCandidate:
    measure_idx: int
    slot_idx: int
    time_sec: float
    note_type: int
    priority: float
    source: str
    pitch: int | None = None


def _target_to_note(target: TaikoTarget, velocity: float, cfg: AppConfig, enable_big: bool) -> int:
    if target == "ignore":
        return TaikoNote.REST
    if target == "big_don":
        return TaikoNote.BIG_DON if enable_big else TaikoNote.DON
    if target == "big_kat":
        return TaikoNote.BIG_KAT if enable_big else TaikoNote.KAT
    if target == "don":
        if enable_big and velocity >= min(0.98, cfg.preset.big_note_threshold + 0.08):
            return TaikoNote.BIG_DON
        return TaikoNote.DON
    if target == "kat":
        if enable_big and velocity >= min(0.98, cfg.preset.big_note_threshold + 0.08):
            return TaikoNote.BIG_KAT
        return TaikoNote.KAT
    return TaikoNote.REST


def _effective_drum_target(hit: DrumHit, cfg: AppConfig) -> TaikoTarget:
    target = cfg.preset.drum_mapping.get(hit.drum_class, "don")
    if hit.drum_class == "tom" and target != "ignore":
        if hit.pitch in _HIGH_TOM_PITCHES:
            return "kat"
        if hit.pitch in _LOW_TOM_PITCHES:
            return "don"
    return target


def _drum_priority(hit: DrumHit) -> float:
    base = {
        "snare": 1.0,
        "kick": 0.95,
        "crash": 0.92,
        "tom": 0.88,
        "ride": 0.25,
        "hihat": 0.20,
        "unknown": 0.50,
    }.get(hit.drum_class, 0.50)
    return base * (0.5 + 0.5 * hit.velocity)


def _shape_drum_fills(
    placed: dict[tuple[int, int], PlacedCandidate],
    slots: list[MeasureSlot],
) -> None:
    """Break up Don-heavy drum fills into natural Ka/Don Taiko sticking."""
    by_measure: dict[int, list[tuple[int, PlacedCandidate]]] = {}
    for (m_idx, s_idx), cand in placed.items():
        by_measure.setdefault(m_idx, []).append((s_idx, cand))

    for m_idx, items in by_measure.items():
        if len(items) < 4 or m_idx >= len(slots):
            continue
        items.sort(key=lambda pair: pair[0])
        grid_size = max(1, slots[m_idx].grid_size)

        # Find contiguous rapid fill clusters (<= 0.19s between adjacent hits)
        clusters: list[list[tuple[int, PlacedCandidate]]] = []
        cur: list[tuple[int, PlacedCandidate]] = [items[0]]
        for pair in items[1:]:
            if pair[1].time_sec - cur[-1][1].time_sec <= 0.19:
                cur.append(pair)
            else:
                if len(cur) >= 4:
                    clusters.append(cur)
                cur = [pair]
        if len(cur) >= 4:
            clusters.append(cur)

        for cluster in clusters:
            has_fill_voice = any(c.source in ("drum:snare", "drum:tom") for _, c in cluster)
            late_or_dense = cluster[-1][0] >= int(grid_size * 0.5) or len(cluster) >= 6
            if not (has_fill_voice and late_or_dense):
                continue

            # If a rapid fill has >=3 consecutive Dons (e.g. unpitched toms or kick+tom clusters),
            # introduce musical Ka alternation on non-downbeat hits.
            don_streak = 0
            for idx_in_cluster, (s_idx, cand) in enumerate(cluster):
                is_don = cand.note_type in (TaikoNote.DON, TaikoNote.BIG_DON)
                is_kat = cand.note_type in (TaikoNote.KAT, TaikoNote.BIG_KAT)
                if is_don:
                    don_streak += 1
                    # Keep beat 1 intact; shape unpitched toms or 3rd+ consecutive Dons into Ka accents
                    if s_idx > 0 and (
                        (cand.source == "drum:tom" and cand.pitch is None and idx_in_cluster % 2 == 0)
                        or don_streak >= 3
                    ):
                        cand.note_type = TaikoNote.KAT
                        don_streak = 0
                elif is_kat:
                    don_streak = 0


def map_drum_hits(
    drum_hits: list[DrumHit],
    slots: list[MeasureSlot],
    plans: list[MeasurePlan],
    cfg: AppConfig,
) -> dict[tuple[int, int], PlacedCandidate]:
    """Map DrumHits onto quantized cells with tom-pitch & fill-sticking intelligence."""
    placed: dict[tuple[int, int], PlacedCandidate] = {}

    for hit in drum_hits:
        loc = time_to_measure_slot(hit.time_sec, slots)
        if loc is None:
            continue
        m_idx, s_idx = loc
        plan = plans[m_idx]

        if plan.follow_mode == "vocals":
            continue

        eff_vel = min(1.0, hit.velocity * plan.drum_sensitivity)
        if hit.drum_class in ("hihat", "ride") and cfg.preset.drum_mapping.get(hit.drum_class) == "ignore":
            continue
        if eff_vel < 0.25:
            continue

        target = _effective_drum_target(hit, cfg)
        note = _target_to_note(target, eff_vel, cfg, plan.enable_big_notes)
        if note == TaikoNote.REST:
            continue

        prio = _drum_priority(hit) * plan.drum_sensitivity
        key = (m_idx, s_idx)
        existing = placed.get(key)

        if existing is None:
            placed[key] = PlacedCandidate(
                measure_idx=m_idx,
                slot_idx=s_idx,
                time_sec=hit.time_sec,
                note_type=note,
                priority=prio,
                source=f"drum:{hit.drum_class}",
                pitch=hit.pitch,
            )
        else:
            pair = {existing.source, f"drum:{hit.drum_class}"}
            # Kick + Crash/Snare collision on strong beats: promote to Big Note if enabled
            if plan.enable_big_notes and "drum:kick" in pair and ("drum:crash" in pair or "drum:snare" in pair):
                is_strong_beat = (s_idx % max(1, slots[m_idx].grid_size // 4)) == 0
                if "drum:crash" in pair and (hit.velocity >= cfg.preset.big_note_threshold or existing.priority >= 0.85):
                    existing.note_type = TaikoNote.BIG_KAT
                    existing.priority = max(existing.priority, prio) + 0.15
                    existing.source = "drum:slam"
                    continue
                if "drum:snare" in pair and is_strong_beat and (hit.velocity >= cfg.preset.big_note_threshold or existing.priority >= 0.85):
                    existing.note_type = TaikoNote.BIG_DON
                    existing.priority = max(existing.priority, prio) + 0.15
                    existing.source = "drum:slam"
                    continue

            # Preserve tom pitch/class over background kick in rapid fills
            if existing.source == "drum:tom" and hit.drum_class == "kick" and s_idx > 0:
                continue
            if existing.source == "drum:kick" and hit.drum_class == "tom" and s_idx > 0:
                placed[key] = PlacedCandidate(
                    measure_idx=m_idx,
                    slot_idx=s_idx,
                    time_sec=hit.time_sec,
                    note_type=note,
                    priority=max(prio, existing.priority + 0.01),
                    source=f"drum:{hit.drum_class}",
                    pitch=hit.pitch,
                )
                continue

            if prio > existing.priority:
                placed[key] = PlacedCandidate(
                    measure_idx=m_idx,
                    slot_idx=s_idx,
                    time_sec=hit.time_sec,
                    note_type=note,
                    priority=prio,
                    source=f"drum:{hit.drum_class}",
                    pitch=hit.pitch,
                )

    _shape_drum_fills(placed, slots)
    _balance_don_heavy_tom_grooves(placed)
    return placed


def _balance_don_heavy_tom_grooves(
    placed: dict[tuple[int, int], PlacedCandidate],
) -> None:
    """Prevent monotonous Don walls in dense tom-heavy grooves (e.g. LinnDrum/surf beats)."""
    by_measure: dict[int, list[tuple[int, PlacedCandidate]]] = {}
    for (m_idx, s_idx), cand in placed.items():
        by_measure.setdefault(m_idx, []).append((s_idx, cand))

    for items in by_measure.values():
        if len(items) < 6:
            continue
        items.sort(key=lambda pair: pair[0])
        dons = sum(1 for _, c in items if c.note_type in (TaikoNote.DON, TaikoNote.BIG_DON))
        if dons / len(items) < 0.85:
            continue
        toms = [(s_idx, c) for s_idx, c in items if s_idx > 0 and c.source == "drum:tom"]
        for idx, (_, cand) in enumerate(toms):
            if idx % 2 == 1:
                cand.note_type = TaikoNote.KAT