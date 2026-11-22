"""Post-ergonomics groove triple-stroke pass for repetitive punk/rock beats."""

from __future__ import annotations

from taikolab.arranger.mapper_drums import PlacedCandidate
from taikolab.arranger.section_builder import MeasurePlan
from taikolab.schemas import ChartMeasure, DifficultyLevel, TaikoNote

_DON = TaikoNote.DON.value
_KAT = TaikoNote.KAT.value
_BIG_DON = TaikoNote.BIG_DON.value
_BIG_KAT = TaikoNote.BIG_KAT.value
_HIT_CODES = frozenset({_DON, _KAT, _BIG_DON, _BIG_KAT})
_ROLL_CODES = frozenset({
    TaikoNote.DRUMROLL.value,
    TaikoNote.BIG_DRUMROLL.value,
    TaikoNote.BALLOON.value,
    TaikoNote.END.value,
})


def _max_bpm_for(difficulty: DifficultyLevel, configured_max_bpm: float) -> float:
    if difficulty == "normal":
        return max(120.0, configured_max_bpm - 15.0)
    return configured_max_bpm


def _find_pockets(
    notes: list[int],
    next_bar_notes: list[int] | None,
    step_unit: int,
) -> list[tuple[str, int]]:
    """Find relative 16-step pockets anywhere in the bar (handles pickup-shifted grooves like 2,4,6,14)."""
    def idx(slot16: int) -> int:
        return slot16 * step_unit

    def is_hit(slot16: int) -> bool:
        return 0 <= slot16 < 16 and notes[idx(slot16)] in _HIT_CODES

    def is_big(slot16: int) -> bool:
        return 0 <= slot16 < 16 and notes[idx(slot16)] in (_BIG_DON, _BIG_KAT)

    def is_empty(slot16: int) -> bool:
        if slot16 < 0:
            return True
        if slot16 >= 16:
            if next_bar_notes is None:
                return True
            nxt_idx = (slot16 - 16) * step_unit
            return nxt_idx >= len(next_bar_notes) or next_bar_notes[nxt_idx] == 0
        s0 = idx(slot16)
        return all(notes[s0 + k] == 0 for k in range(step_unit))

    pockets: list[tuple[str, int]] = []
    for s in range(0, 12):
        # A) Paired 8th pocket: hit(s), empty(s+1), hit(s+2), empty(s+3), hit(s+4) -> d-d-d _ k
        if (
            is_empty(s - 1)
            and is_hit(s)
            and is_empty(s + 1)
            and is_hit(s + 2)
            and not is_big(s + 2)
            and is_empty(s + 3)
            and is_hit(s + 4)
        ):
            pockets.append(("paired_fwd", s))

        # B) Open forward triple from anchor s: hit(s), empty(s+1..s+3), and hit(s+4) or empty(s+4..s+5)
        if (
            is_empty(s - 1)
            and is_hit(s)
            and is_empty(s + 1)
            and is_empty(s + 2)
            and is_empty(s + 3)
            and ((is_hit(s + 4) and is_empty(s + 5)) or (is_empty(s + 4) and is_empty(s + 5)))
        ):
            pockets.append(("open_fwd", s))

        # C) Open reverse triple: hit(s), empty(s+1..s+3), non-big-hit/empty(s+4), empty(s+5) -> k _ d-d-d
        if (
            is_empty(s - 1)
            and is_hit(s)
            and is_empty(s + 1)
            and is_empty(s + 2)
            and is_empty(s + 3)
            and not is_big(s + 4)
            and is_empty(s + 5)
            and (is_hit(s + 4) or is_empty(s + 6))
        ):
            pockets.append(("open_rev", s))

    return pockets


def _is_eligible_bar(
    measure: ChartMeasure,
    next_measure: ChartMeasure | None,
    plan: MeasurePlan,
    difficulty: DifficultyLevel,
) -> bool:
    if measure.numerator != 4 or measure.denominator != 4:
        return False
    grid = len(measure.notes)
    if grid not in (16, 32):
        return False
    if plan.low_energy_guitar or plan.is_blast_zone:
        return False
    if any(n in _ROLL_CODES for n in measure.notes):
        return False
    if measure.bpm > _max_bpm_for(difficulty, plan.groove_triple_max_bpm):
        return False

    step_unit = grid // 16
    active_slots = [idx for idx, n in enumerate(measure.notes) if n in _HIT_CODES]
    if not (3 <= len(active_slots) <= 6):
        return False
    dur = max(0.1, measure.end_sec - measure.start_sec)
    if (len(active_slots) / dur) >= 6.5:
        return False

    # Skip dense/gallop bars that already contain adjacent 16th-note runs (e.g. NOFX d-d-k)
    for i in range(len(active_slots) - 1):
        if (active_slots[i + 1] - active_slots[i]) <= step_unit:
            return False

    next_notes = next_measure.notes if next_measure else None
    return bool(_find_pockets(measure.notes, next_notes, step_unit))


def _drum_rhythm_signature(
    drum_candidates: dict[tuple[int, int], PlacedCandidate],
    measure_idx: int,
    grid_size: int,
) -> tuple[tuple[int, int], ...]:
    step_unit = max(1, grid_size // 16)
    return tuple(
        sorted(
            (s_idx // step_unit, cand.note_type)
            for (m_i, s_idx), cand in drum_candidates.items()
            if m_i == measure_idx and cand.source.startswith("drum:") and cand.note_type in _HIT_CODES
        )
    )


def _select_weighted_bars(
    candidates: list[tuple[int, bool]],
    total_bars: int,
    cap: int,
    ramp: float,
) -> list[int]:
    if cap <= 0 or not candidates:
        return []
    if len(candidates) <= cap:
        return [m for m, _ in candidates]
    if cap == 1:
        prim = [m for m, is_p in candidates if is_p] or [m for m, _ in candidates]
        frac = 0.5 + 0.38 * ramp
        return [prim[min(len(prim) - 1, round((len(prim) - 1) * frac))]]

    denom = max(1, total_bars - 1)
    weights = [
        ((1.0 - ramp) + ramp * (0.12 + 1.88 * ((m_idx / denom) ** 1.6))) * (2.2 if is_p else 1.0)
        for m_idx, is_p in candidates
    ]
    total_w = sum(weights)
    if total_w <= 0.0:
        return [m for m, _ in candidates[:cap]]

    cum_w: list[float] = []
    acc = 0.0
    for w in weights:
        acc += w
        cum_w.append(acc)

    selected_indices: list[int] = []
    used: set[int] = set()
    for k in range(cap):
        target_w = ((k + 0.5) / cap) * total_w
        best_i = min(
            (i for i in range(len(candidates)) if i not in used),
            key=lambda i: abs(cum_w[i] - target_w),
        )
        used.add(best_i)
        selected_indices.append(best_i)

    selected_indices.sort()
    return [candidates[i][0] for i in selected_indices]


def _collect_repeat_candidates(
    eligible: list[bool],
    sigs: list[tuple[tuple[int, int], ...]],
) -> list[tuple[int, bool]]:
    """Return sorted (measure_idx, is_primary_alternating_bar) across repeating runs."""
    n = len(eligible)
    in_repeat = [False] * n
    for i in range(n):
        if not eligible[i] or not sigs[i]:
            continue
        for j in range(max(0, i - 4), min(n, i + 5)):
            if j != i and sigs[j] == sigs[i]:
                in_repeat[i] = True
                break

    runs: list[list[int]] = []
    cur: list[int] = []
    for i in range(n):
        if in_repeat[i]:
            cur.append(i)
        else:
            if len(cur) >= 3:
                runs.append(cur)
            cur = []
    if len(cur) >= 3:
        runs.append(cur)

    candidates: list[tuple[int, bool]] = []
    for run_idx, run in enumerate(runs):
        prefer_odd = (run_idx % 2 == 0)
        for pos, m_idx in enumerate(run):
            if m_idx == 0:
                continue
            is_primary = ((pos % 2 == 1) == prefer_odd)
            candidates.append((m_idx, is_primary))
    return candidates


def _select_target_bars(
    eligible: list[bool],
    sigs: list[tuple[tuple[int, int], ...]],
    cap: int | None = None,
    ramp: float = 0.50,
    density: float | None = None,
) -> list[int]:
    """Pick repeating groove bars using density (0..1) or explicit cap, weighted by ending ramp."""
    candidates = _collect_repeat_candidates(eligible, sigs)
    if not candidates:
        return []
    if density is not None:
        if density <= 0.0:
            return []
        target_cap = len(candidates) if density >= 0.999 else max(1, round(len(candidates) * density))
    else:
        target_cap = cap or 0
    if target_cap <= 0:
        return []
    return _select_weighted_bars(candidates, len(eligible), target_cap, ramp)


def _pattern_pools(complexity: float) -> tuple[list[tuple[int, int, int, int | None]], list[tuple[int, int, int]]]:
    low_fwd = [(_DON, _DON, _DON, None), (_KAT, _KAT, _KAT, None)]
    med_fwd = low_fwd + [
        (_DON, _DON, _KAT, _DON),
        (_KAT, _KAT, _DON, _KAT),
        (_DON, _DON, _DON, _KAT),
        (_KAT, _KAT, _KAT, _DON),
    ]
    high_fwd = med_fwd + [(_DON, _KAT, _DON, _KAT), (_KAT, _DON, _KAT, _DON)]

    low_rev = [(_DON, _DON, _DON), (_KAT, _KAT, _KAT)]
    med_rev = low_rev + [(_DON, _DON, _KAT), (_KAT, _KAT, _DON)]
    high_rev = med_rev + [(_DON, _KAT, _DON), (_KAT, _DON, _KAT)]

    if complexity <= 0.30:
        return low_fwd, low_rev
    if complexity <= 0.68:
        return med_fwd, med_rev
    return high_fwd, high_rev


def _try_apply_pocket(
    notes: list[int],
    next_bar_notes: list[int] | None,
    step_unit: int,
    complexity: float,
    variant_seed: int,
    difficulty: DifficultyLevel,
) -> bool:
    pockets = _find_pockets(notes, next_bar_notes, step_unit)
    if not pockets:
        return False

    fwd_pool, rev_pool = _pattern_pools(complexity)

    def idx(slot16: int) -> int:
        return slot16 * step_unit

    def is_big(slot16: int) -> bool:
        return notes[idx(slot16)] in (_BIG_DON, _BIG_KAT)

    kind, s = pockets[variant_seed % len(pockets)]

    def apply_pocket(p_kind: str, p_start: int, seed: int) -> bool:
        anchor_note = notes[idx(p_start)]
        base_color = _KAT if anchor_note in (_KAT, _BIG_KAT) else _DON

        if p_kind in ("paired_fwd", "open_fwd"):
            if complexity <= 0.30 or p_start == 0:
                pool = [p for p in fwd_pool if p[0] == base_color] or fwd_pool
            else:
                pool = fwd_pool
            t0, t1, t2, end_c = pool[seed % len(pool)]
            if not is_big(p_start):
                notes[idx(p_start)] = t0
            notes[idx(p_start + 1)] = t1
            notes[idx(p_start + 2)] = t2
            if notes[idx(p_start + 4)] == 0:
                notes[idx(p_start + 4)] = end_c if end_c is not None else (_KAT if t0 == _DON else _DON)
            elif end_c is not None and not is_big(p_start + 4):
                notes[idx(p_start + 4)] = end_c
            return True

        if p_kind == "open_rev":
            if complexity <= 0.30 or p_start == 0:
                other = _DON if base_color == _KAT else _KAT
                pool = [p for p in rev_pool if p[0] == other] or rev_pool
            else:
                pool = rev_pool
            t0, t1, t2 = pool[seed % len(pool)]
            if not is_big(p_start) and p_start != 0:
                notes[idx(p_start)] = _KAT if t0 == _DON else _DON
            notes[idx(p_start + 2)] = t0
            notes[idx(p_start + 3)] = t1
            if not is_big(p_start + 4):
                notes[idx(p_start + 4)] = t2
            return True

        return False

    placed = apply_pocket(kind, s, variant_seed)

    # On Oni at high complexity, optionally embellish a second non-overlapping pocket >= 6 steps away
    if placed and difficulty in ("oni", "ura") and complexity >= 0.80:
        second = next(((k, s2) for k, s2 in pockets if abs(s2 - s) >= 6), None)
        if second is not None:
            apply_pocket(second[0], second[1], variant_seed + 1)

    return placed


def apply_groove_triples(
    measures: list[ChartMeasure],
    drum_candidates: dict[tuple[int, int], PlacedCandidate],
    plans: list[MeasurePlan],
    difficulty: DifficultyLevel,
) -> list[ChartMeasure]:
    """Inject Taiko-style 16th-note triple-stroke accents into sparse repeating grooves."""
    if difficulty == "easy" or not measures or not plans:
        return measures

    densities = [p.groove_triple_density for p in plans if p.groove_triple_density is not None]
    caps = [p.max_groove_triples for p in plans if p.max_groove_triples is not None]
    density = max(densities) if densities else None
    cap = max(caps) if caps else None
    if (density is None or density <= 0.0) and (cap is None or cap <= 0):
        return measures
    complexity = max(0.0, min(1.0, plans[0].groove_triple_complexity))
    ramp = max(0.0, min(1.0, plans[0].groove_triple_ramp))

    eligible = [
        _is_eligible_bar(m, measures[i + 1] if i + 1 < len(measures) else None, plans[i], difficulty)
        for i, m in enumerate(measures)
    ]
    sigs = [
        _drum_rhythm_signature(drum_candidates, i, len(m.notes))
        for i, m in enumerate(measures)
    ]
    target_indices = _select_target_bars(eligible, sigs, cap=cap, ramp=ramp, density=density)
    if not target_indices:
        return measures

    out = [m.model_copy(deep=True) for m in measures]
    for seq_idx, m_idx in enumerate(target_indices):
        m = out[m_idx]
        step_unit = max(1, len(m.notes) // 16)
        next_notes = out[m_idx + 1].notes if m_idx + 1 < len(out) else None
        _try_apply_pocket(
            m.notes,
            next_notes,
            step_unit=step_unit,
            complexity=complexity,
            variant_seed=seq_idx + m_idx,
            difficulty=difficulty,
        )

    return out