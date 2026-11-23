"""Drum-driven Taiko mapping with hands-over-feet streams, tom-pitch shaping and solo/quiet cymbal rescue."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

from taikolab.arranger.quantizer import MeasureSlot, time_to_measure_slot
from taikolab.arranger.section_builder import MeasurePlan
from taikolab.config import AppConfig
from taikolab.schemas import DrumHit, TaikoNote, TaikoTarget

_HIGH_TOM_PITCHES = frozenset({47, 48, 50})
_LOW_TOM_PITCHES = frozenset({41, 43, 45})
_SOLO_CYMBAL_CLEARANCE_SEC = 0.250
_QUIET_CYMBAL_CLEARANCE_SEC = 0.065


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


def _nearest_time_distance(sorted_times: list[float], t: float) -> float:
    if not sorted_times:
        return float("inf")
    pos = bisect_left(sorted_times, t)
    best = float("inf")
    if pos < len(sorted_times):
        best = min(best, abs(sorted_times[pos] - t))
    if pos > 0:
        best = min(best, abs(t - sorted_times[pos - 1]))
    return best


def _demote_stream_big_notes(
    placed: dict[tuple[int, int], PlacedCandidate],
) -> None:
    """Hands Over Feet: in rapid 16th streams (Delta t <= 0.19s), demote Big Notes and let hands (snare/tom) win."""
    by_measure: dict[int, list[tuple[int, PlacedCandidate]]] = {}
    for (m_idx, s_idx), cand in placed.items():
        by_measure.setdefault(m_idx, []).append((s_idx, cand))

    for items in by_measure.values():
        if len(items) < 3:
            continue
        items.sort(key=lambda pair: pair[0])
        for idx, (s_idx, cand) in enumerate(items):
            prev_close = (idx > 0) and (cand.time_sec - items[idx - 1][1].time_sec <= 0.19)
            next_close = (idx + 1 < len(items)) and (items[idx + 1][1].time_sec - cand.time_sec <= 0.19)
            if prev_close or next_close:
                # Inside any rapid stream or fill: big notes are strictly forbidden
                if cand.note_type == TaikoNote.BIG_DON:
                    cand.note_type = TaikoNote.DON
                elif cand.note_type == TaikoNote.BIG_KAT:
                    cand.note_type = TaikoNote.KAT
                # If this slot had snare or tom hands alongside a kick pedal, hands win!
                if "snare" in cand.source or cand.pitch in (37, 38, 39, 40):
                    cand.note_type = TaikoNote.KAT


def _shape_drum_fills(
    placed: dict[tuple[int, int], PlacedCandidate],
    slots: list[MeasureSlot],
) -> None:
    """Break up Don-heavy drum fills into natural Ka/Don Taiko sticking, strictly favoring hands."""
    by_measure: dict[int, list[tuple[int, PlacedCandidate]]] = {}
    for (m_idx, s_idx), cand in placed.items():
        by_measure.setdefault(m_idx, []).append((s_idx, cand))

    for m_idx, items in by_measure.items():
        if len(items) < 4 or m_idx >= len(slots):
            continue
        items.sort(key=lambda pair: pair[0])
        grid_size = max(1, slots[m_idx].grid_size)

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

            # Strip any big notes inside the fill cluster
            for _, cand in cluster:
                if cand.note_type == TaikoNote.BIG_DON:
                    cand.note_type = TaikoNote.DON
                elif cand.note_type == TaikoNote.BIG_KAT:
                    cand.note_type = TaikoNote.KAT

            # Hands Over Feet: In any dense 16th stream (>=4 hits), hands (snare/tom) strictly override foot kicks!
            for s_idx, cand in cluster:
                if cand.source in ("drum:snare", "drum:slam"):
                    cand.note_type = TaikoNote.KAT
                    cand.source = "drum:snare"
                elif cand.note_type in (TaikoNote.BIG_DON, TaikoNote.BIG_KAT):
                    cand.note_type = TaikoNote.DON if cand.note_type == TaikoNote.BIG_DON else TaikoNote.KAT

            don_streak = 0
            for idx_in_cluster, (s_idx, cand) in enumerate(cluster):
                is_don = cand.note_type in (TaikoNote.DON, TaikoNote.BIG_DON)
                is_kat = cand.note_type in (TaikoNote.KAT, TaikoNote.BIG_KAT)
                if is_don:
                    don_streak += 1
                    if s_idx > 0 and (
                        (cand.source == "drum:tom" and cand.pitch is None and idx_in_cluster % 2 == 0)
                        or don_streak >= 3
                    ):
                        cand.note_type = TaikoNote.KAT
                        don_streak = 0
                elif is_kat:
                    don_streak = 0


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


def _rescue_solo_and_quiet_cymbals(
    placed: dict[tuple[int, int], PlacedCandidate],
    ignored_cymbals: list[DrumHit],
    core_times: list[float],
    slots: list[MeasureSlot],
    plans: list[MeasurePlan],
    cfg: AppConfig,
) -> None:
    """Place quiet time-keeping hi-hats/rides and solo cymbal hits clear of kick/snare/tom."""
    if not ignored_cymbals:
        return

    core_count_by_bar: dict[int, int] = {}
    for m_idx, _ in placed:
        core_count_by_bar[m_idx] = core_count_by_bar.get(m_idx, 0) + 1

    for hit in ignored_cymbals:
        loc = time_to_measure_slot(hit.time_sec, slots)
        if loc is None:
            continue
        m_idx, s_idx = loc
        plan = plans[m_idx]
        if plan.follow_mode in ("vocals", "guitar", "vocals_guitar") or plan.difficulty == "easy" or plan.low_energy_guitar:
            continue
        if hit.drum_class == "ride" and plan.ride_cymbal_mode == "ignore":
            continue

        eff_vel = min(1.0, hit.velocity * plan.drum_sensitivity)
        key = (m_idx, s_idx)
        if key in placed:
            continue

        bar_cores = core_count_by_bar.get(m_idx, 0)
        dist = _nearest_time_distance(core_times, hit.time_sec)
        beat_step = max(1, slots[m_idx].grid_size // max(1, slots[m_idx].numerator))

        if bar_cores <= 2:
            if eff_vel < 0.42 or dist < _QUIET_CYMBAL_CLEARANCE_SEC:
                continue
            if plan.difficulty == "normal" and (s_idx % beat_step) != 0:
                continue
            placed[key] = PlacedCandidate(
                measure_idx=m_idx,
                slot_idx=s_idx,
                time_sec=hit.time_sec,
                note_type=TaikoNote.KAT,
                priority=0.58 * eff_vel,
                source=f"drum:{hit.drum_class}",
                pitch=hit.pitch,
            )
            continue

        if dist < _SOLO_CYMBAL_CLEARANCE_SEC:
            continue
        is_open_hat_or_ride = hit.drum_class == "ride" or hit.pitch == 46
        if plan.difficulty == "normal":
            min_vel = 0.72 if is_open_hat_or_ride else 1.1
        else:
            min_vel = 0.55 if is_open_hat_or_ride else max(0.82, cfg.preset.hihat_min_velocity * 0.90)
        if eff_vel < min_vel:
            continue

        placed[key] = PlacedCandidate(
            measure_idx=m_idx,
            slot_idx=s_idx,
            time_sec=hit.time_sec,
            note_type=TaikoNote.KAT,
            priority=0.60 * eff_vel,
            source=f"drum:{hit.drum_class}",
            pitch=hit.pitch,
        )


def map_drum_hits(
    drum_hits: list[DrumHit],
    slots: list[MeasureSlot],
    plans: list[MeasurePlan],
    cfg: AppConfig,
) -> dict[tuple[int, int], PlacedCandidate]:
    """Map DrumHits onto cells with Hands-Over-Feet fills, tom-pitch, and solo cymbal intelligence."""
    placed: dict[tuple[int, int], PlacedCandidate] = {}
    ignored_cymbals: list[DrumHit] = []
    core_times: list[float] = []

    for hit in drum_hits:
        loc = time_to_measure_slot(hit.time_sec, slots)
        if loc is None:
            continue
        m_idx, s_idx = loc
        plan = plans[m_idx]

        if plan.follow_mode in ("vocals", "guitar", "vocals_guitar"):
            continue

        eff_vel = min(1.0, hit.velocity * plan.drum_sensitivity)
        if hit.drum_class in ("hihat", "ride") and cfg.preset.drum_mapping.get(hit.drum_class) == "ignore":
            ignored_cymbals.append(hit)
            continue
        if eff_vel < 0.25:
            continue

        target = _effective_drum_target(hit, cfg)
        eff_vel_big = eff_vel + (0.12 if (plan.gp_accent and (s_idx % max(1, slots[m_idx].grid_size // 4)) == 0) else 0.0)
        note = _target_to_note(target, min(1.0, eff_vel_big), cfg, plan.enable_big_notes)
        if note == TaikoNote.REST:
            continue

        core_times.append(hit.time_sec)
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
            # Measure downbeat (s_idx == 0): Kick + Crash / Snare favors Big Don
            if s_idx == 0 and plan.enable_big_notes and "drum:kick" in pair and ("drum:crash" in pair or "drum:snare" in pair):
                if hit.velocity >= cfg.preset.big_note_threshold or existing.priority >= 0.85:
                    existing.note_type = TaikoNote.BIG_DON
                    existing.priority = max(existing.priority, prio) + 0.15
                    existing.source = "drum:slam"
                    continue

            # Measure backbeat slams on strong quarter beats
            if s_idx > 0 and plan.enable_big_notes and "drum:kick" in pair and "drum:snare" in pair:
                is_strong_beat = (s_idx % max(1, slots[m_idx].grid_size // 4)) == 0
                if is_strong_beat and (hit.velocity >= cfg.preset.big_note_threshold or existing.priority >= 0.85):
                    existing.note_type = TaikoNote.BIG_DON
                    existing.priority = max(existing.priority, prio) + 0.15
                    existing.source = "drum:slam"
                    continue

            if s_idx > 0 and "drum:kick" in pair and "drum:tom" in pair:
                tom_hit = hit if hit.drum_class == "tom" else None
                tom_target = _effective_drum_target(tom_hit, cfg) if tom_hit else "kat"
                existing.note_type = TaikoNote.KAT if tom_target == "kat" else TaikoNote.DON
                existing.priority = max(existing.priority, prio)
                existing.source = "drum:tom"
                continue

            if s_idx == 0 and "drum:kick" in pair:
                if existing.source != "drum:kick":
                    existing.note_type = TaikoNote.BIG_DON if (plan.enable_big_notes and note in (TaikoNote.BIG_DON, TaikoNote.BIG_KAT)) else TaikoNote.DON
                    existing.source = "drum:kick"
                    existing.priority = max(existing.priority, prio)
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

    core_times.sort()
    _rescue_solo_and_quiet_cymbals(placed, ignored_cymbals, core_times, slots, plans, cfg)
    _shape_drum_fills(placed, slots)
    _balance_don_heavy_tom_grooves(placed)
    _demote_stream_big_notes(placed)
    return placed