"""Diagnostic summary & playability report for generated TaikoCharts."""

from __future__ import annotations

from dataclasses import dataclass

from taikolab.schemas import ChartDifficulty, TaikoChart, TaikoNote


@dataclass
class CourseStats:
    difficulty: str
    stars: int
    measures: int
    total_notes: int
    don_count: int
    kat_count: int
    big_count: int
    roll_count: int
    avg_nps: float
    peak_nps: float
    min_bpm: float
    max_bpm: float
    meters: list[str]


def analyze_course(course: ChartDifficulty) -> CourseStats:
    if not course.measures:
        return CourseStats(
            difficulty=course.difficulty,
            stars=course.level_stars,
            measures=0,
            total_notes=0,
            don_count=0,
            kat_count=0,
            big_count=0,
            roll_count=0,
            avg_nps=0.0,
            peak_nps=0.0,
            min_bpm=0.0,
            max_bpm=0.0,
            meters=[],
        )

    don = kat = big = rolls = 0
    peak_nps = 0.0
    bpms: list[float] = []
    meters_seen: list[str] = []

    total_start = course.measures[0].start_sec
    total_end = course.measures[-1].end_sec
    total_dur = max(0.1, total_end - total_start)

    for m in course.measures:
        bpms.append(m.bpm)
        meter = f"{m.numerator}/{m.denominator}"
        if meter not in meters_seen:
            meters_seen.append(meter)

        bar_notes = 0
        for n in m.notes:
            if n == TaikoNote.DON:
                don += 1
                bar_notes += 1
            elif n == TaikoNote.KAT:
                kat += 1
                bar_notes += 1
            elif n == TaikoNote.BIG_DON:
                don += 1
                big += 1
                bar_notes += 1
            elif n == TaikoNote.BIG_KAT:
                kat += 1
                big += 1
                bar_notes += 1
            elif n in (TaikoNote.DRUMROLL, TaikoNote.BIG_DRUMROLL):
                rolls += 1

        bar_dur = max(0.05, m.end_sec - m.start_sec)
        peak_nps = max(peak_nps, bar_notes / bar_dur)

    total_notes = don + kat
    return CourseStats(
        difficulty=course.difficulty,
        stars=course.level_stars,
        measures=len(course.measures),
        total_notes=total_notes,
        don_count=don,
        kat_count=kat,
        big_count=big,
        roll_count=rolls,
        avg_nps=round(total_notes / total_dur, 2),
        peak_nps=round(peak_nps, 2),
        min_bpm=round(min(bpms), 1),
        max_bpm=round(max(bpms), 1),
        meters=meters_seen,
    )


def format_chart_report(chart: TaikoChart) -> str:
    lines = [
        f"=== TaikoLab Report: {chart.title} ({chart.subtitle}) ===",
        f"Base BPM: {chart.base_bpm:.1f} | Offset: {chart.offset_sec:.3f}s | Audio: {chart.wave_filename}",
    ]
    for course in chart.courses:
        s = analyze_course(course)
        lines.append(
            f"  [{s.difficulty.upper():6s} {s.stars}*] "
            f"Notes={s.total_notes} (Don={s.don_count}, Kat={s.kat_count}, Big={s.big_count}, Rolls={s.roll_count}) | "
            f"NPS avg={s.avg_nps:.1f} peak={s.peak_nps:.1f} | "
            f"BPM={s.min_bpm:.0f}-{s.max_bpm:.0f} | Meters={','.join(s.meters)}"
        )
    return "\n".join(lines)