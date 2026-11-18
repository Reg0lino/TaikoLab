"""Beat & downbeat tracking via Beat This! (no madmom) + tempo region segmentation."""

from __future__ import annotations

import statistics
from pathlib import Path

from taikolab.ingest.stem_loader import ensure_wav_path
from taikolab.schemas import BeatEvent, TempoRegion

MIN_BAR_DURATION_SEC = 0.65


def build_clean_downbeat_spans(
    events: list[BeatEvent],
    duration_sec: float,
) -> list[tuple[float, float, int, int, int]]:
    """Return clean (start, end, beat_count, num, den) spans without 360+ BPM or 68 BPM glitches."""
    all_times = sorted(e.time_sec for e in events)
    raw_dbs = sorted(e.time_sec for e in events if e.is_downbeat)

    # Filter out 1-beat or < 0.65s micro-downbeats
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
    for i in range(len(clean_dbs)):
        start = clean_dbs[i]
        if i + 1 < len(clean_dbs):
            end = clean_dbs[i + 1]
        else:
            if duration_sec - start < MIN_BAR_DURATION_SEC:
                continue
            end = min(duration_sec, start + (clean_dbs[i] - clean_dbs[i - 1]))
        dur = end - start
        if dur < MIN_BAR_DURATION_SEC:
            continue

        bar_beats = [t for t in all_times if start - 0.025 <= t < end - 0.025]
        # If Beat This! missed a downbeat and merged two 4/4 bars (7-8 beats), split cleanly at midpoint beat
        if len(bar_beats) >= 7 and dur >= 1.8:
            mid_idx = len(bar_beats) // 2
            mid_t = bar_beats[mid_idx]
            if (mid_t - start) >= MIN_BAR_DURATION_SEC and (end - mid_t) >= MIN_BAR_DURATION_SEC:
                spans.append((start, mid_t, mid_idx))
                spans.append((mid_t, end, len(bar_beats) - mid_idx))
                continue

        spans.append((start, end, len(bar_beats)))

    # Infer accurate meter so 2-beat short spans become 2/4 instead of 360 BPM 4/4
    out: list[tuple[float, float, int, int, int]] = []
    for start, end, b_count in spans:
        dur = end - start
        if b_count == 2 and dur >= 1.30:
            num, den = 6, 8
        elif b_count == 2:
            num, den = 2, 4
        elif b_count == 3:
            num, den = 3, 4
        elif b_count == 6:
            num, den = 6, 8
        elif b_count in (5, 7):
            num, den = b_count, 8 if dur < 2.2 else 4
        else:
            num, den = 4, 4
        out.append((start, end, b_count, num, den))

    return out


def track_beats_and_regions(
    audio_path: Path,
    duration_sec: float,
    device: str = "cuda",
) -> tuple[list[BeatEvent], list[TempoRegion]]:
    """Run Beat This! File2Beats and segment into TempoRegions."""
    import torch
    from beat_this.inference import File2Beats

    chosen_device = device if (device == "cuda" and torch.cuda.is_available()) else "cpu"
    tracker = File2Beats(checkpoint_path="final0", device=chosen_device, dbn=False)

    with ensure_wav_path(audio_path) as wav_path:
        beats_arr, downbeats_arr = tracker(str(wav_path))

    beats_list = sorted(float(t) for t in beats_arr if 0.0 <= float(t) <= duration_sec)
    raw_dbs = sorted(float(t) for t in downbeats_arr if 0.0 <= float(t) <= duration_sec)

    events: list[BeatEvent] = []
    db_set_used: set[int] = set()
    for bt in beats_list:
        is_db = False
        for idx, db in enumerate(raw_dbs):
            if idx not in db_set_used and abs(bt - db) <= 0.045:
                is_db = True
                db_set_used.add(idx)
                break
        events.append(BeatEvent(time_sec=round(bt, 4), is_downbeat=is_db))

    for idx, db in enumerate(raw_dbs):
        if idx not in db_set_used:
            events.append(BeatEvent(time_sec=round(db, 4), is_downbeat=True))
    events.sort(key=lambda e: e.time_sec)

    regions = infer_tempo_regions(events, duration_sec)
    return events, regions


def infer_tempo_regions(events: list[BeatEvent], duration_sec: float) -> list[TempoRegion]:
    """Group consecutive downbeat spans with similar BPM & beat count into TempoRegions."""
    spans = build_clean_downbeat_spans(events, duration_sec)

    if not spans:
        all_t = [e.time_sec for e in events]
        if len(all_t) >= 2:
            med_ibi = statistics.median(all_t[i + 1] - all_t[i] for i in range(len(all_t) - 1))
            bpm = max(40.0, min(300.0, 60.0 / max(0.15, med_ibi)))
        else:
            bpm = 140.0
        return [TempoRegion(start_sec=0.0, end_sec=duration_sec, bpm=round(bpm, 2), numerator=4, denominator=4)]

    bar_summaries: list[tuple[float, float, float, int, int]] = []
    for start, end, _, num, den in spans:
        dur = end - start
        bpm = max(40.0, min(280.0, 240.0 * num / (den * dur)))
        bar_summaries.append((start, end, bpm, num, den))

    # Enforce clean-dump meter stability: require >=3 consecutive bars before switching away from majority meter
    if len(bar_summaries) >= 3:
        smoothed: list[tuple[float, float, float, int, int]] = [bar_summaries[0]]
        for i in range(1, len(bar_summaries) - 1):
            prev_m = (bar_summaries[i - 1][3], bar_summaries[i - 1][4])
            cur_s, cur_e, cur_bpm, cur_n, cur_d = bar_summaries[i]
            next_m = (bar_summaries[i + 1][3], bar_summaries[i + 1][4])
            if (cur_n, cur_d) != prev_m and (prev_m == next_m or cur_n in (5, 7, 2)):
                if not (cur_n == 2 and (cur_e - cur_s) < 1.0):
                    cur_n, cur_d = prev_m
                    cur_bpm = max(40.0, min(280.0, 240.0 * cur_n / (cur_d * (cur_e - cur_s))))
            smoothed.append((cur_s, cur_e, cur_bpm, cur_n, cur_d))
        smoothed.append(bar_summaries[-1])
        bar_summaries = smoothed

    regions: list[TempoRegion] = []
    grp_start, grp_end, grp_bpms, grp_num, grp_den = (
        0.0,
        bar_summaries[0][1],
        [bar_summaries[0][2]],
        bar_summaries[0][3],
        bar_summaries[0][4],
    )

    for start, end, bpm, num, den in bar_summaries[1:]:
        cur_med = statistics.median(grp_bpms)
        same_meter = (num == grp_num and den == grp_den)
        close_bpm = abs(bpm - cur_med) <= max(12.0, cur_med * 0.16)
        if same_meter and close_bpm:
            grp_end = end
            grp_bpms.append(bpm)
        else:
            regions.append(
                TempoRegion(
                    start_sec=round(grp_start, 4),
                    end_sec=round(start, 4),
                    bpm=round(statistics.median(grp_bpms), 2),
                    numerator=grp_num,
                    denominator=grp_den,
                )
            )
            grp_start, grp_end, grp_bpms, grp_num, grp_den = start, end, [bpm], num, den

    regions.append(
        TempoRegion(
            start_sec=round(grp_start, 4),
            end_sec=round(duration_sec, 4),
            bpm=round(statistics.median(grp_bpms), 2),
            numerator=grp_num,
            denominator=grp_den,
        )
    )
    return regions