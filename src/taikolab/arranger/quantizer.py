"""Absolute-time measure slicing, human tap-downbeat priority & subdivision quantization."""

from __future__ import annotations

from dataclasses import dataclass

from taikolab.schemas import (
    BeatEvent,
    ChartMeasure,
    DownbeatMarker,
    SectionOverride,
    SongAnalysis,
    SongOverrides,
    TempoRegion,
)

MIN_BAR_DURATION_SEC = 0.65
MIN_TAP_GAP_SEC = 0.35


@dataclass
class MeasureSlot:
    measure_idx: int
    start_sec: float
    end_sec: float
    bpm: float
    numerator: int
    denominator: int
    grid_size: int


def choose_grid_size(numerator: int, denominator: int, bpm: float) -> int:
    if denominator == 8:
        return max(8, numerator * 2)
    if numerator == 2 and denominator == 4:
        return 8
    if numerator == 3 and denominator == 4:
        return 24 if bpm < 100.0 else 12
    if numerator == 4 and denominator == 4:
        return 32 if bpm < 105.0 else 16
    return max(8, numerator * 4)


def _active_region_at(t: float, regions: list[TempoRegion]) -> TempoRegion | None:
    for r in regions:
        if r.start_sec <= t < r.end_sec:
            return r
    return regions[-1] if regions else None


def _active_section_at(t: float, overrides: SongOverrides | None) -> SectionOverride | None:
    if not overrides:
        return None
    for sec in overrides.sections:
        if sec.start_sec <= t < sec.end_sec:
            return sec
    return None


def _is_valid_short_measure(
    start_sec: float,
    end_sec: float,
    regions: list[TempoRegion],
) -> bool:
    region = _active_region_at(start_sec + 0.02, regions)
    if region is None:
        return False

    expected_duration = 240.0 * region.numerator / (region.denominator * region.bpm)
    actual_duration = end_sec - start_sec
    tolerance = max(0.08, expected_duration * 0.12)
    return (
        expected_duration < MIN_BAR_DURATION_SEC
        and abs(actual_duration - expected_duration) <= tolerance
    )


def _resolve_meter_and_bpm(
    time_sec: float,
    dur: float,
    default_num: int,
    default_den: int,
    overrides: SongOverrides | None,
) -> tuple[float, int, int]:
    num = default_num
    den = default_den
    explicit_bpm: float | None = None
    mult = 1.0

    if overrides:
        if overrides.tempo_anchors:
            active = None
            for anc in sorted(overrides.tempo_anchors, key=lambda a: a.time_sec):
                if anc.time_sec <= time_sec + 1e-4:
                    active = anc
            if active is not None:
                if active.bpm is not None:
                    explicit_bpm = active.bpm
                mult *= active.bpm_multiplier
                num = active.numerator
                den = active.denominator

        sec = _active_section_at(time_sec, overrides)
        if sec is not None:
            if sec.numerator is not None:
                num = sec.numerator
            if sec.denominator is not None:
                den = sec.denominator
            if sec.bpm is not None:
                explicit_bpm = sec.bpm
            mult *= sec.bpm_multiplier

    bpm = explicit_bpm if explicit_bpm is not None else (240.0 * num / (den * dur))
    bpm *= mult
    return max(25.0, min(480.0, bpm)), num, den


def _build_slots_from_custom_downbeats(
    markers: list[DownbeatMarker],
    analysis: SongAnalysis,
    overrides: SongOverrides,
    regions: list[TempoRegion],
) -> list[MeasureSlot]:
    sorted_m = sorted(markers, key=lambda m: m.time_sec)
    clean: list[DownbeatMarker] = []
    for m in sorted_m:
        if not clean or (m.time_sec - clean[-1].time_sec) >= MIN_TAP_GAP_SEC:
            clean.append(m)

    if len(clean) < 2:
        return []

    slots: list[MeasureSlot] = []
    idx = 0
    while idx < len(clean):
        cur = clean[idx]
        start = cur.time_sec
        if idx + 1 < len(clean):
            end = clean[idx + 1].time_sec
        else:
            prev_dur = clean[idx].time_sec - clean[idx - 1].time_sec
            rem = analysis.duration_sec - start
            if rem < max(0.45, prev_dur * 0.75):
                break
            end = min(analysis.duration_sec, start + prev_dur)

        dur = end - start
        if dur < MIN_TAP_GAP_SEC:
            idx += 1
            continue

        sec = _active_section_at(start + 0.02, overrides)
        reg = _active_region_at(start + 0.02, regions)

        if cur.numerator is not None and cur.denominator is not None:
            num, den = cur.numerator, cur.denominator
        elif sec is not None and sec.numerator is not None and sec.denominator is not None:
            num, den = sec.numerator, sec.denominator
        elif reg is not None:
            num, den = reg.numerator, reg.denominator
        else:
            num, den = 4, 4

        raw_bpm = max(25.0, min(480.0, 240.0 * num / (den * dur)))
        grid = choose_grid_size(num, den, raw_bpm)
        slots.append(MeasureSlot(len(slots), start, end, raw_bpm, num, den, grid))
        idx += 1

    return slots


def _build_downbeat_spans(
    beats: list[BeatEvent],
    duration_sec: float,
    regions: list[TempoRegion],
) -> list[tuple[float, float, int]]:
    all_times = [b.time_sec for b in beats]
    raw_dbs = [b.time_sec for b in beats if b.is_downbeat]

    clean_dbs: list[float] = []
    for db in raw_dbs:
        if not clean_dbs:
            clean_dbs.append(db)
            continue
        beats_between = [t for t in all_times if clean_dbs[-1] - 0.02 <= t < db - 0.02]
        if (
            (db - clean_dbs[-1]) >= MIN_BAR_DURATION_SEC and len(beats_between) >= 2
        ) or _is_valid_short_measure(clean_dbs[-1], db, regions):
            clean_dbs.append(db)

    if len(clean_dbs) < 2:
        return []

    spans: list[tuple[float, float, int]] = []
    for idx in range(len(clean_dbs)):
        start = clean_dbs[idx]
        if idx + 1 < len(clean_dbs):
            end = clean_dbs[idx + 1]
        else:
            prev_dur = clean_dbs[idx] - clean_dbs[idx - 1]
            rem = duration_sec - start
            if rem < max(MIN_BAR_DURATION_SEC, prev_dur * 0.75):
                continue
            end = min(duration_sec, start + max(MIN_BAR_DURATION_SEC, prev_dur))
        dur = end - start
        if dur < MIN_BAR_DURATION_SEC and not _is_valid_short_measure(start, end, regions):
            continue

        bar_beats = [t for t in all_times if start - 0.025 <= t < end - 0.025]
        if len(bar_beats) >= 7 and dur >= 1.8:
            mid_idx = len(bar_beats) // 2
            mid_t = bar_beats[mid_idx]
            if (mid_t - start) >= MIN_BAR_DURATION_SEC and (end - mid_t) >= MIN_BAR_DURATION_SEC:
                spans.append((start, mid_t, mid_idx))
                spans.append((mid_t, end, len(bar_beats) - mid_idx))
                continue

        spans.append((start, end, len(bar_beats)))
    return spans


def _apply_section_shifts_to_slots(
    slots: list[MeasureSlot],
    overrides: SongOverrides | None,
) -> list[MeasureSlot]:
    if not slots or not overrides:
        return slots
    if not any(abs(sec.offset_shift_ms) > 0.01 for sec in overrides.sections):
        return slots

    shifts = []
    for slot in slots:
        mid = 0.5 * (slot.start_sec + slot.end_sec)
        sec = _active_section_at(mid, overrides)
        shifts.append((sec.offset_shift_ms / 1000.0) if sec and abs(sec.offset_shift_ms) > 0.01 else 0.0)

    barlines = [max(0.0, slots[0].start_sec + shifts[0])]
    for i in range(1, len(slots)):
        shift = shifts[i] if abs(shifts[i]) > 0.01 else shifts[i - 1]
        barlines.append(max(barlines[-1] + 0.12, slots[i].start_sec + shift))
    barlines.append(max(barlines[-1] + 0.12, slots[-1].end_sec + shifts[-1]))

    out: list[MeasureSlot] = []
    for i, slot in enumerate(slots):
        start = barlines[i]
        end = barlines[i + 1]
        dur = max(0.12, end - start)
        bpm = max(25.0, min(480.0, 240.0 * slot.numerator / (slot.denominator * dur)))
        out.append(
            MeasureSlot(
                measure_idx=slot.measure_idx,
                start_sec=start,
                end_sec=end,
                bpm=bpm,
                numerator=slot.numerator,
                denominator=slot.denominator,
                grid_size=slot.grid_size,
            )
        )
    return out


def _apply_barline_times_to_slots(
    slots: list[MeasureSlot],
    overrides: SongOverrides | None,
) -> list[MeasureSlot]:
    if not slots or not overrides or len(overrides.barline_times) != len(slots) + 1:
        return slots
    times = overrides.barline_times
    for i in range(len(slots)):
        if times[i + 1] - times[i] < 0.12:
            return slots
    out: list[MeasureSlot] = []
    for i, slot in enumerate(slots):
        start = float(times[i])
        end = float(times[i + 1])
        dur = end - start
        bpm = max(25.0, min(480.0, 240.0 * slot.numerator / (slot.denominator * dur)))
        out.append(
            MeasureSlot(
                measure_idx=slot.measure_idx,
                start_sec=start,
                end_sec=end,
                bpm=bpm,
                numerator=slot.numerator,
                denominator=slot.denominator,
                grid_size=slot.grid_size,
            )
        )
    return out


def _apply_section_bpm_rules(
    slots: list[MeasureSlot],
    overrides: SongOverrides | None,
) -> list[MeasureSlot]:
    if not slots or not overrides or not overrides.sections:
        return slots
    needs_rule = any(
        sec.bpm is not None or sec.bpm_multiplier >= 1.8 or sec.bpm_multiplier <= 0.6
        for sec in overrides.sections
    )
    if not needs_rule:
        return slots

    out: list[MeasureSlot] = []
    idx = 0
    while idx < len(slots):
        slot = slots[idx]
        mid = 0.5 * (slot.start_sec + slot.end_sec)
        sec = _active_section_at(mid, overrides)

        if sec is not None and sec.bpm is not None:
            group: list[MeasureSlot] = [slot]
            j = idx + 1
            while j < len(slots):
                next_mid = 0.5 * (slots[j].start_sec + slots[j].end_sec)
                if _active_section_at(next_mid, overrides) is sec:
                    group.append(slots[j])
                    j += 1
                else:
                    break
            span_start = group[0].start_sec
            span_end = group[-1].end_sec
            span_dur = max(0.20, span_end - span_start)
            num = sec.numerator if sec.numerator is not None else group[0].numerator
            den = sec.denominator if sec.denominator is not None else group[0].denominator
            target_bar_dur = max(0.18, 240.0 * num / (den * sec.bpm))
            count = max(1, round(span_dur / target_bar_dur))
            actual_dur = span_dur / count
            actual_bpm = max(25.0, min(480.0, 240.0 * num / (den * actual_dur)))
            grid = choose_grid_size(num, den, actual_bpm)
            for k in range(count):
                b_start = span_start + k * actual_dur
                b_end = span_end if k == count - 1 else span_start + (k + 1) * actual_dur
                out.append(
                    MeasureSlot(
                        measure_idx=len(out),
                        start_sec=b_start,
                        end_sec=b_end,
                        bpm=actual_bpm,
                        numerator=num,
                        denominator=den,
                        grid_size=grid,
                    )
                )
            idx = j
            continue

        if sec is not None and sec.bpm_multiplier >= 1.8:
            dur = slot.end_sec - slot.start_sec
            if dur >= 0.40:
                half_dur = dur * 0.5
                mid_t = slot.start_sec + half_dur
                half_bpm = max(25.0, min(480.0, 240.0 * slot.numerator / (slot.denominator * half_dur)))
                grid = choose_grid_size(slot.numerator, slot.denominator, half_bpm)
                out.append(
                    MeasureSlot(
                        measure_idx=len(out),
                        start_sec=slot.start_sec,
                        end_sec=mid_t,
                        bpm=half_bpm,
                        numerator=slot.numerator,
                        denominator=slot.denominator,
                        grid_size=grid,
                    )
                )
                out.append(
                    MeasureSlot(
                        measure_idx=len(out),
                        start_sec=mid_t,
                        end_sec=slot.end_sec,
                        bpm=half_bpm,
                        numerator=slot.numerator,
                        denominator=slot.denominator,
                        grid_size=grid,
                    )
                )
                idx += 1
                continue

        if sec is not None and sec.bpm_multiplier <= 0.6 and idx + 1 < len(slots):
            nxt = slots[idx + 1]
            nxt_mid = 0.5 * (nxt.start_sec + nxt.end_sec)
            if _active_section_at(nxt_mid, overrides) is sec:
                merged_dur = nxt.end_sec - slot.start_sec
                merged_bpm = max(25.0, min(480.0, 240.0 * slot.numerator / (slot.denominator * merged_dur)))
                grid = choose_grid_size(slot.numerator, slot.denominator, merged_bpm)
                out.append(
                    MeasureSlot(
                        measure_idx=len(out),
                        start_sec=slot.start_sec,
                        end_sec=nxt.end_sec,
                        bpm=merged_bpm,
                        numerator=slot.numerator,
                        denominator=slot.denominator,
                        grid_size=grid,
                    )
                )
                idx += 2
                continue

        real_dur = max(0.12, slot.end_sec - slot.start_sec)
        real_bpm = max(25.0, min(480.0, 240.0 * slot.numerator / (slot.denominator * real_dur)))
        out.append(
            MeasureSlot(
                measure_idx=len(out),
                start_sec=slot.start_sec,
                end_sec=slot.end_sec,
                bpm=real_bpm,
                numerator=slot.numerator,
                denominator=slot.denominator,
                grid_size=slot.grid_size,
            )
        )
        idx += 1

    return out


def build_base_and_warped_slots(
    analysis: SongAnalysis,
    overrides: SongOverrides | None = None,
) -> tuple[list[MeasureSlot], list[MeasureSlot]]:
    """Return unwarped base slots and final slots after section shifts and barline warps."""
    base = _build_unwarped_measure_slots(analysis, overrides)
    base = _apply_section_bpm_rules(base, overrides)
    warped = _apply_section_shifts_to_slots(base, overrides)
    warped = _apply_barline_times_to_slots(warped, overrides)
    return base, warped


def build_measure_slots(
    analysis: SongAnalysis,
    overrides: SongOverrides | None = None,
) -> list[MeasureSlot]:
    """Build contiguous MeasureSlots: always trust score/section meter (never guess random 2/4/3/4)."""
    _, warped = build_base_and_warped_slots(analysis, overrides)
    return warped


def _build_unwarped_measure_slots(
    analysis: SongAnalysis,
    overrides: SongOverrides | None = None,
) -> list[MeasureSlot]:
    regions = sorted(analysis.tempo_regions, key=lambda r: r.start_sec)

    if overrides and len(overrides.custom_downbeats) >= 2:
        tapped_slots = _build_slots_from_custom_downbeats(
            overrides.custom_downbeats, analysis, overrides, regions
        )
        if tapped_slots:
            return tapped_slots

    beats = sorted(analysis.beats, key=lambda b: b.time_sec)
    has_explicit_anchors = bool(overrides and overrides.tempo_anchors)

    spans = (
        _build_downbeat_spans(beats, analysis.duration_sec, regions)
        if not has_explicit_anchors
        else []
    )
    if spans:
        slots: list[MeasureSlot] = []
        idx = 0
        while idx < len(spans):
            start, end, _ = spans[idx]
            dur = end - start
            reg = _active_region_at(start, regions)
            sec = _active_section_at(start + 0.02, overrides)

            # Strictly trust score region meter or section override (never guess random 2/4 or 3/4)
            if sec is not None and sec.numerator is not None and sec.denominator is not None:
                num, den = sec.numerator, sec.denominator
            elif reg is not None:
                num, den = reg.numerator, reg.denominator
            else:
                num, den = 4, 4

            bpm, num, den = _resolve_meter_and_bpm(start + 0.02, dur, num, den, overrides)
            grid = choose_grid_size(num, den, bpm)
            slots.append(
                MeasureSlot(
                    measure_idx=len(slots),
                    start_sec=start,
                    end_sec=end,
                    bpm=bpm,
                    numerator=num,
                    denominator=den,
                    grid_size=grid,
                )
            )
            idx += 1
        if slots:
            return slots

    slots = []
    t = beats[0].time_sec if beats else 0.0
    if overrides and overrides.tempo_anchors:
        t = min(t, overrides.tempo_anchors[0].time_sec) if beats else overrides.tempo_anchors[0].time_sec

    while t < analysis.duration_sec - MIN_BAR_DURATION_SEC:
        reg = _active_region_at(t, regions)
        base_bpm = reg.bpm if reg else 140.0
        base_num = reg.numerator if reg else 4
        base_den = reg.denominator if reg else 4

        bpm, num, den = _resolve_meter_and_bpm(t, 240.0 * base_num / (base_den * base_bpm), base_num, base_den, overrides)
        dur = 240.0 * num / (den * bpm)
        end = t + dur

        next_boundaries: list[float] = [r.start_sec for r in regions if r.start_sec > t + 0.05]
        if overrides:
            next_boundaries.extend(a.time_sec for a in overrides.tempo_anchors if a.time_sec > t + 0.05)
        if next_boundaries:
            nearest = min(next_boundaries)
            if nearest < end and (nearest - t) >= MIN_BAR_DURATION_SEC:
                end = nearest
                bpm = max(25.0, min(480.0, 240.0 * num / (den * (end - t))))

        if end > analysis.duration_sec + dur * 0.5:
            break

        slots.append(
            MeasureSlot(
                measure_idx=len(slots),
                start_sec=t,
                end_sec=end,
                bpm=bpm,
                numerator=num,
                denominator=den,
                grid_size=choose_grid_size(num, den, bpm),
            )
        )
        t = end

    return slots


def time_to_measure_slot(
    time_sec: float,
    slots: list[MeasureSlot],
) -> tuple[int, int] | None:
    if not slots:
        return None
    if time_sec < slots[0].start_sec - 0.08 or time_sec > slots[-1].end_sec + 0.05:
        return None
    if time_sec < slots[0].start_sec:
        return (0, 0)

    for idx, m in enumerate(slots):
        if m.start_sec <= time_sec < m.end_sec:
            dur = m.end_sec - m.start_sec
            rel = (time_sec - m.start_sec) / dur
            slot = int(round(rel * m.grid_size))
            if slot >= m.grid_size:
                if idx + 1 < len(slots):
                    return (idx + 1, 0)
                return (idx, m.grid_size - 1)
            return (idx, max(0, slot))

    return (len(slots) - 1, slots[-1].grid_size - 1)


def slots_to_empty_measures(slots: list[MeasureSlot]) -> list[ChartMeasure]:
    return [
        ChartMeasure(
            measure_idx=s.measure_idx,
            start_sec=s.start_sec,
            end_sec=s.end_sec,
            bpm=s.bpm,
            numerator=s.numerator,
            denominator=s.denominator,
            scroll=1.0,
            notes=[0] * s.grid_size,
        )
        for s in slots
    ]