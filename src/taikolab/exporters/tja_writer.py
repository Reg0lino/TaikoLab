"""OpenTaiko (.tja) serializer with drift correction and normalized highway speed."""

from __future__ import annotations

import shutil
from pathlib import Path

from taikolab.schemas import ChartDifficulty, ChartMeasure, DifficultyLevel, TaikoChart, TaikoNote

_COURSE_MAP: dict[DifficultyLevel, str] = {
    "easy": "Easy",
    "normal": "Normal",
    "hard": "Hard",
    "oni": "Oni",
    "ura": "Edit",
}
_MAX_MEASURE_TIMING_DRIFT_SEC = 0.100
_MAX_SIMULATED_DRIFT_BEFORE_BPM_CHANGE_SEC = 0.050


def measure_duration_sec(numerator: int, denominator: int, bpm: float) -> float:
    return 240.0 * float(numerator) / (float(denominator) * float(bpm))


def _sanitize_measures_rolls(measures: list[ChartMeasure]) -> list[list[int]]:
    grids = [list(m.notes) for m in measures]
    in_roll = False

    for grid in grids:
        for s_idx, val in enumerate(grid):
            if val in (TaikoNote.DRUMROLL, TaikoNote.BIG_DRUMROLL, TaikoNote.BALLOON):
                if in_roll:
                    grid[s_idx] = TaikoNote.REST
                else:
                    in_roll = True
            elif val == TaikoNote.END:
                if in_roll:
                    in_roll = False
                else:
                    grid[s_idx] = TaikoNote.REST
            elif in_roll and val != TaikoNote.REST:
                grid[s_idx] = TaikoNote.REST

    if in_roll and grids:
        last_grid = grids[-1]
        closed = False
        for idx in range(len(last_grid) - 1, -1, -1):
            if last_grid[idx] == TaikoNote.REST:
                last_grid[idx] = TaikoNote.END
                closed = True
                break
        if not closed:
            last_grid[-1] = TaikoNote.END

    return grids


def render_course_tja(
    course: ChartDifficulty,
    initial_bpm: float,
    base_scroll: float = 0.500,
    bpm_deadzone: float = 1.8,
    normalize_scroll: bool = False,
    target_highway_speed: float = 101.343,
) -> list[str]:
    """Render course with gentle fractional drift nudge (no spikes, no cumulative drift)."""
    lines: list[str] = []
    course_name = _COURSE_MAP.get(course.difficulty, "Oni")
    lines.append(f"COURSE:{course_name}")
    lines.append(f"LEVEL:{course.level_stars}")
    if course.balloon_counts:
        lines.append("BALLOON:" + ",".join(str(x) for x in course.balloon_counts))
    else:
        lines.append("BALLOON:")
    lines.append("SCOREINIT:1000")
    lines.append("SCOREDIFF:250")
    lines.append("")
    lines.append("#START")

    if not course.measures:
        lines.append("#END")
        return lines

    clean_grids = _sanitize_measures_rolls(course.measures)

    active_scroll = (
        round(target_highway_speed / initial_bpm, 3) if normalize_scroll else base_scroll
    )
    lines.append(f"#SCROLL {active_scroll:.3f}")

    active_num = course.measures[0].numerator
    active_den = course.measures[0].denominator
    lines.append(f"#MEASURE {active_num}/{active_den}")

    active_bpm = round(initial_bpm, 3)
    sim_clock = course.measures[0].start_sec
    max_drift_sec = 0.0

    for idx, (m, grid) in enumerate(zip(course.measures, clean_grids)):
        meter_changed = (m.numerator != active_num or m.denominator != active_den)
        if meter_changed:
            active_num = m.numerator
            active_den = m.denominator
            lines.append(f"#MEASURE {active_num}/{active_den}")

        dur = max(0.10, m.end_sec - m.start_sec)
        drift = sim_clock - m.start_sec
        max_drift_sec = max(max_drift_sec, abs(drift))
        # Positive drift means the chart is late, so shorten this measure to catch up.
        nudge = max(-0.025, min(0.025, drift * 0.25))
        effective_dur = max(0.10, dur - nudge)
        bar_bpm = max(30.0, min(380.0, 240.0 * active_num / (active_den * effective_dur)))
        projected_end_drift = (
            sim_clock + measure_duration_sec(active_num, active_den, active_bpm) - m.end_sec
        )

        if (
            idx == 0
            or meter_changed
            or abs(bar_bpm - active_bpm) >= bpm_deadzone
            or abs(projected_end_drift) > _MAX_SIMULATED_DRIFT_BEFORE_BPM_CHANGE_SEC
        ):
            active_bpm = round(bar_bpm, 3)
            lines.append(f"#BPMCHANGE {active_bpm:.3f}")

        if normalize_scroll:
            normalized_scroll = round(target_highway_speed / active_bpm, 3)
            if normalized_scroll != round(active_scroll, 3):
                active_scroll = normalized_scroll
                lines.append(f"#SCROLL {active_scroll:.3f}")

        sim_clock += measure_duration_sec(active_num, active_den, active_bpm)

        row_str = "".join(str(int(n)) for n in grid)
        lines.append(f"{row_str},")

    max_drift_sec = max(max_drift_sec, abs(sim_clock - course.measures[-1].end_sec))
    if max_drift_sec > _MAX_MEASURE_TIMING_DRIFT_SEC:
        raise ValueError(
            f"{course_name} TJA tempo map drifts {max_drift_sec:.3f}s from the intended grid "
            f"(limit {_MAX_MEASURE_TIMING_DRIFT_SEC:.3f}s)."
        )

    lines.append("#END")
    return lines


def render_tja_text(
    chart: TaikoChart,
    base_scroll: float = 0.500,
    bpm_deadzone: float = 1.8,
    normalize_scroll: bool = False,
    target_highway_speed: float = 101.343,
    **kwargs,
) -> str:
    tja_offset = -float(chart.offset_sec)
    if round(tja_offset, 3) == 0.0:
        tja_offset = 0.0

    header = [
        f"TITLE:{chart.title}",
        f"SUBTITLE:--{chart.subtitle}" if chart.subtitle else "SUBTITLE:--",
        f"BPM:{chart.base_bpm:.3f}",
        f"WAVE:{chart.wave_filename}",
        f"OFFSET:{tja_offset:.3f}",
        f"DEMOSTART:{chart.demo_start_sec:.3f}",
        f"GENRE:{chart.genre}",
        "",
    ]

    body: list[str] = []
    for course in chart.courses:
        body.extend(
            render_course_tja(
                course=course,
                initial_bpm=chart.base_bpm,
                base_scroll=base_scroll,
                bpm_deadzone=bpm_deadzone,
                normalize_scroll=normalize_scroll,
                target_highway_speed=target_highway_speed,
            )
        )
        body.append("")

    return "\n".join(header + body).rstrip() + "\n"


def write_tja_file(
    chart: TaikoChart,
    output_path: Path,
    base_scroll: float = 0.500,
    bpm_deadzone: float = 1.8,
    normalize_scroll: bool = False,
    target_highway_speed: float = 101.343,
    **kwargs,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    text = render_tja_text(
        chart,
        base_scroll=base_scroll,
        bpm_deadzone=bpm_deadzone,
        normalize_scroll=normalize_scroll,
        target_highway_speed=target_highway_speed,
    )
    output_path.write_text(text, encoding="utf-8")
    return output_path


def export_to_opentaiko(
    tja_path: Path,
    audio_path: Path | None,
    opentaiko_songs_dir: str,
    folder_name: str | None = None,
) -> Path | None:
    if not opentaiko_songs_dir.strip():
        return None
    dest_root = Path(opentaiko_songs_dir)
    dest_dir = dest_root / (folder_name or tja_path.stem)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_tja = dest_dir / tja_path.name
    shutil.copy2(tja_path, dest_tja)
    if audio_path and audio_path.exists():
        shutil.copy2(audio_path, dest_dir / audio_path.name)
    return dest_tja