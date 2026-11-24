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

    is_halftime_32 = (grid == 32 and measure.bpm < 135.0)
    step_unit = 1 if is_halftime_32 else (grid // 16)
    max_hits = max(4, min(10, plan.groove_triple_max_hits))

    if is_halftime_32:
        h1 = [n for n in measure.notes[:16] if n in _HIT_CODES]
        h2 = [n for n in measure.notes[16:] if n in _HIT_CODES]
        if not ((3 <= len(h1) <= max_hits) or (3 <= len(h2) <= max_hits)):
            return False
        dur = max(0.1, measure.end_sec - measure.start_sec)
        total_hits = len(h1) + len(h2)
        nps_cap = 7.5 if max_hits <= 6 else (7.5 + (max_hits - 6) * 1.1)
        if (total_hits / dur) >= nps_cap:
            return False
        return bool(
            _find_pockets(measure.notes[:16], measure.notes[16:], 1)
            or _find_pockets(measure.notes[16:], next_measure.notes[:16] if next_measure else None, 1)
        )

    active_slots = [idx for idx, n in enumerate(measure.notes) if n in _HIT_CODES]
    if not (3 <= len(active_slots) <= max_hits):
        return False
    dur = max(0.1, measure.end_sec - measure.start_sec)
    nps_cap = 6.5 if max_hits <= 6 else (6.5 + (max_hits - 6) * 1.1)
    if (len(active_slots) / dur) >= nps_cap:
        return False

    if max_hits <= 6:
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
    norm_color = {_DON: _DON, _BIG_DON: _DON, _KAT: _KAT, _BIG_KAT: _KAT}
    return tuple(
        sorted(
            (s_idx // step_unit, norm_color[cand.note_type])
            for (m_i, s_idx), cand in drum_candidates.items()
            if m_i == measure_idx and cand.source.startswith("drum:") and cand.note_type in _HIT_CODES
        )
    )


def _sigs_match_groove(
    sig_a: tuple[tuple[int, int], ...],
    sig_b: tuple[tuple[int, int], ...],
    is_halftime: bool,
) -> bool:
    if sig_a == sig_b:
        return True
    if not is_halftime or not sig_a or not sig_b:
        return False
    a1 = tuple((s, c) for s, c in sig_a if s < 8)
    a2 = tuple((s - 8, c) for s, c in sig_a if s >= 8)
    b1 = tuple((s, c) for s, c in sig_b if s < 8)
    b2 = tuple((s - 8, c) for s, c in sig_b if s >= 8)
    return (len(a1) >= 3 and a1 in (b1, b2)) or (len(a2) >= 3 and a2 in (b1, b2))


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
    halftime_flags: list[bool] | None = None,
) -> list[tuple[int, bool]]:
    """Return sorted (measure_idx, is_primary_alternating_bar) across repeating runs."""
    n = len(eligible)
    in_repeat = [False] * n
    for i in range(n):
        if not eligible[i] or not sigs[i]:
            continue
        is_ht = bool(halftime_flags and halftime_flags[i])
        for j in range(max(0, i - 4), min(n, i + 5)):
            if j != i and _sigs_match_groove(sigs[i], sigs[j], is_ht):
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

    clean_pockets = [p for p in pockets if not is_big(p[1]) and not is_big(p[1] + 4)]
    active_pockets = clean_pockets or pockets
    kind, s = active_pockets[variant_seed % len(active_pockets)]

    def _demote_big(slot16: int) -> None:
        val = notes[idx(slot16)]
        if val == _BIG_DON:
            notes[idx(slot16)] = _DON
        elif val == _BIG_KAT:
            notes[idx(slot16)] = _KAT

    def apply_pocket(p_kind: str, p_start: int, seed: int) -> bool:
        anchor_note = notes[idx(p_start)]
        base_color = _KAT if anchor_note in (_KAT, _BIG_KAT) else _DON

        if p_kind in ("paired_fwd", "open_fwd"):
            if complexity <= 0.30 or p_start == 0:
                pool = [p for p in fwd_pool if p[0] == base_color] or fwd_pool
            else:
                pool = fwd_pool
            t0, t1, t2, end_c = pool[seed % len(pool)]
            notes[idx(p_start)] = base_color if p_start == 0 else t0
            notes[idx(p_start + 1)] = t1
            notes[idx(p_start + 2)] = t2
            if notes[idx(p_start + 4)] == 0:
                notes[idx(p_start + 4)] = end_c if end_c is not None else (_KAT if notes[idx(p_start)] == _DON else _DON)
            elif end_c is not None:
                notes[idx(p_start + 4)] = end_c
            else:
                _demote_big(p_start + 4)
            _demote_big(p_start)
            return True

        if p_kind == "open_rev":
            if complexity <= 0.30 or p_start == 0:
                other = _DON if base_color == _KAT else _KAT
                pool = [p for p in rev_pool if p[0] == other] or rev_pool
            else:
                pool = rev_pool
            t0, t1, t2 = pool[seed % len(pool)]
            if p_start != 0:
                notes[idx(p_start)] = _KAT if t0 == _DON else _DON
            else:
                _demote_big(p_start)
            notes[idx(p_start + 2)] = t0
            notes[idx(p_start + 3)] = t1
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
    max_den = max(densities) if densities else None
    cap = max(caps) if caps else None
    if (max_den is None or max_den <= 0.0) and (cap is None or cap <= 0):
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
    ht_flags = [(len(m.notes) == 32 and m.bpm < 135.0) for m in measures]
    distinct_densities = {p.groove_triple_density for p in plans}
    if len(distinct_densities) <= 1 or cap is not None:
        cands = _collect_repeat_candidates(eligible, sigs, ht_flags)
        if not cands:
            target_indices = []
        elif max_den is not None and cap is None:
            t_cap = len(cands) if max_den >= 0.999 else (0 if max_den <= 0.0 else max(1, round(len(cands) * max_den)))
            target_indices = _select_weighted_bars(cands, len(measures), t_cap, ramp)
        else:
            target_indices = _select_weighted_bars(cands, len(measures), cap or 0, ramp)
    else:
        all_cands = _collect_repeat_candidates(eligible, sigs, ht_flags)
        target_indices = []
        g_start = 0
        while g_start < len(plans):
            g_den = plans[g_start].groove_triple_density
            g_end = g_start + 1
            while g_end < len(plans) and plans[g_end].groove_triple_density == g_den:
                g_end += 1
            if g_den is not None and g_den > 0.0:
                group_cands = [(m, is_p) for m, is_p in all_cands if g_start <= m < g_end]
                if group_cands:
                    g_cap = len(group_cands) if g_den >= 0.999 else max(1, round(len(group_cands) * g_den))
                    target_indices.extend(_select_weighted_bars(group_cands, len(measures), g_cap, ramp))
            g_start = g_end
        target_indices.sort()
    if not target_indices:
        return measures

    out = [m.model_copy(deep=True) for m in measures]
    for seq_idx, m_idx in enumerate(target_indices):
        m = out[m_idx]
        is_ht = (len(m.notes) == 32 and m.bpm < 135.0)
        if is_ht:
            first_half = list(m.notes[:16])
            second_half = list(m.notes[16:])
            nxt = out[m_idx + 1].notes[:16] if m_idx + 1 < len(out) else None
            prefer_second = (seq_idx % 2 == 1)
            if prefer_second:
                applied = _try_apply_pocket(second_half, nxt, 1, complexity, seq_idx + m_idx + 1, difficulty)
                if not applied or (difficulty in ("oni", "ura") and complexity >= 0.65):
                    _try_apply_pocket(first_half, second_half, 1, complexity, seq_idx + m_idx, difficulty)
            else:
                applied = _try_apply_pocket(first_half, second_half, 1, complexity, seq_idx + m_idx, difficulty)
                if not applied or (difficulty in ("oni", "ura") and complexity >= 0.65):
                    _try_apply_pocket(second_half, nxt, 1, complexity, seq_idx + m_idx + 1, difficulty)
            m.notes = first_half + second_half
        else:
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