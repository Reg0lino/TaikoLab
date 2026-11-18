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
        return 12
    if numerator == 4 and denominator == 4:
        return 16
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


def _build_downbeat_spans(beats: list[BeatEvent], duration_sec: float) -> list[tuple[float, float, int]]:
    all_times = [b.time_sec for b in beats]
    raw_dbs = [b.time_sec for b in beats if b.is_downbeat]

    clean_dbs: list[float] = []
    for db in raw_dbs:
        if not clean_dbs:
            clean_dbs.append(db)
            continue
        beats_between = [t for t in all_times if clean_dbs[-1] - 0.02 <= t < db - 0.02]
        if (db - clean_dbs[-1]) >= MIN_BAR_DURATION_SEC and len(beats_between) >= 2:
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
        if dur < MIN_BAR_DURATION_SEC:
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


def build_measure_slots(
    analysis: SongAnalysis,
    overrides: SongOverrides | None = None,
) -> list[MeasureSlot]:
    """Build contiguous MeasureSlots: always trust score/section meter (never guess random 2/4/3/4)."""
    regions = sorted(analysis.tempo_regions, key=lambda r: r.start_sec)

    if overrides and len(overrides.custom_downbeats) >= 2:
        tapped_slots = _build_slots_from_custom_downbeats(
            overrides.custom_downbeats, analysis, overrides, regions
        )
        if tapped_slots:
            return tapped_slots

    beats = sorted(analysis.beats, key=lambda b: b.time_sec)
    has_explicit_anchors = bool(overrides and overrides.tempo_anchors)

    spans = _build_downbeat_spans(beats, analysis.duration_sec) if not has_explicit_anchors else []
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