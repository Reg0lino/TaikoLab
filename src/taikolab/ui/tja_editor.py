"""Import and preserve existing TJA files while applying note, timing, level, and course edits."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from math import lcm
from pathlib import Path

from taikolab.exporters.tja_writer import render_course_tja
from taikolab.schemas import ChartDifficulty, ChartMeasure, DifficultyLevel, SongOverrides, TaikoChart, TaikoNote

_COURSES: dict[str, DifficultyLevel] = {
    "easy": "easy",
    "normal": "normal",
    "hard": "hard",
    "oni": "oni",
    "edit": "ura",
}
_COURSE_ORDER: tuple[DifficultyLevel, ...] = ("easy", "normal", "hard", "oni", "ura")
_NOTE_ROW = re.compile(r"^(\s*)([0-8]*)(\s*,.*)$")
_BALLOON_ROW = re.compile(r"^(\s*BALLOON\s*:\s*).*$", re.IGNORECASE)
_OFFSET_ROW = re.compile(r"^(\s*OFFSET\s*:\s*).*$", re.IGNORECASE)
_LEVEL_ROW = re.compile(r"^(\s*LEVEL\s*:\s*).*$", re.IGNORECASE)


@dataclass
class TjaDocument:
    path: Path
    chart: TaikoChart
    lines: list[str]
    newline: str
    trailing_newline: bool
    encoding: str
    rows: dict[tuple[str, int], tuple[int, str, str]]
    original_notes: dict[tuple[str, int], list[int]]
    balloon_rows: dict[str, tuple[int | None, int, list[int]]]
    course_starts: dict[str, int]
    course_spans: dict[str, tuple[int, int]]
    level_lines: dict[str, int]
    offset_line: int | None
    first_course_line: int
    audio_path: Path | None


def load_tja(path: Path) -> TjaDocument:
    path = path.expanduser().resolve()
    if path.suffix.lower() != ".tja" or not path.is_file():
        raise ValueError("Choose an existing .tja chart file.")

    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
        text = raw.decode(encoding)
    else:
        try:
            text = raw.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            text = raw.decode("cp932")
            encoding = "cp932"

    newline = "\r\n" if "\r\n" in text else "\n"
    trailing_newline = text.endswith(("\n", "\r"))
    lines = text.splitlines()
    header: dict[str, str] = {}
    offset_line: int | None = None
    first_course_line = len(lines)
    for line_idx, line in enumerate(lines):
        match = re.match(r"^\s*([A-Za-z_]+)\s*:(.*)$", line)
        if match:
            key = match.group(1).upper()
            if key == "COURSE":
                first_course_line = line_idx
                break
            if key == "OFFSET":
                offset_line = line_idx
            header[key] = match.group(2).strip()

    try:
        base_bpm = float(header["BPM"])
    except (KeyError, ValueError) as exc:
        raise ValueError("The TJA file needs a valid BPM header.") from exc
    if not 20.0 < base_bpm < 500.0:
        raise ValueError("The TJA base BPM must be between 20 and 500.")

    try:
        offset_sec = -float(header.get("OFFSET", "0"))
        demo_start = float(header.get("DEMOSTART", "0"))
    except ValueError as exc:
        raise ValueError("The TJA OFFSET or DEMOSTART value is invalid.") from exc
    start_sec = max(0.0, offset_sec)

    rows: dict[tuple[str, int], tuple[int, str, str]] = {}
    original_notes: dict[tuple[str, int], list[int]] = {}
    balloon_rows: dict[str, tuple[int | None, int, list[int]]] = {}
    course_starts: dict[str, int] = {}
    course_spans: dict[str, tuple[int, int]] = {}
    level_lines: dict[str, int] = {}
    courses: list[ChartDifficulty] = []
    current_name: str | None = None
    current_diff: DifficultyLevel | None = None
    current_level = 5
    current_balloons: list[int] = []
    balloon_line: int | None = None
    course_header_line: int | None = None
    start_line: int | None = None
    measures: list[ChartMeasure] = []
    bpm = base_bpm
    numerator = 4
    denominator = 4
    scroll = 0.5
    clock = start_sec
    in_chart = False

    for line_index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue

        header_match = re.match(r"^\s*([A-Za-z_]+)\s*:(.*)$", line)
        if not in_chart and header_match:
            key = header_match.group(1).upper()
            value = header_match.group(2).strip()
            if key == "COURSE":
                current_name = value
                current_diff = _COURSES.get(value.lower())
                if current_diff is None:
                    raise ValueError(f"Unsupported TJA course: {value}")
                current_level = 5
                current_balloons = []
                balloon_line = None
                course_header_line = line_index
            elif key == "LEVEL" and current_diff:
                level_lines[current_diff] = line_index
                try:
                    current_level = int(value.rstrip("+"))
                except ValueError as exc:
                    raise ValueError(f"Invalid LEVEL value for {current_name}.") from exc
                if not 1 <= current_level <= 12:
                    raise ValueError(f"LEVEL for {current_name} must be between 1 and 12.")
            elif key == "BALLOON" and current_diff:
                balloon_line = line_index
                try:
                    current_balloons = [int(item.strip()) for item in value.split(",") if item.strip()]
                except ValueError as exc:
                    raise ValueError(f"Invalid BALLOON values for {current_name}.") from exc
                if any(count < 1 or count > 1000 for count in current_balloons):
                    raise ValueError(f"BALLOON counts for {current_name} must be between 1 and 1000.")
            continue

        if stripped.upper().startswith("#START"):
            if in_chart:
                raise ValueError("Nested #START commands are not supported.")
            if current_diff is None or any(c.difficulty == current_diff for c in courses):
                raise ValueError(f"Missing or duplicate COURSE before line {line_index + 1}.")
            in_chart = True
            start_line = line_index
            course_starts[current_diff] = line_index
            measures = []
            bpm = base_bpm
            numerator = denominator = 4
            scroll = 0.5
            clock = start_sec
            continue

        if not in_chart:
            continue

        if stripped.upper() == "#END":
            assert current_diff is not None
            courses.append(
                ChartDifficulty(
                    difficulty=current_diff,
                    level_stars=current_level,
                    measures=measures,
                    balloon_counts=current_balloons,
                )
            )
            balloon_rows[current_diff] = (balloon_line, start_line or line_index, list(current_balloons))
            course_spans[current_diff] = (
                course_header_line if course_header_line is not None else (start_line or line_index),
                line_index,
            )
            in_chart = False
            current_diff = None
            current_name = None
            course_header_line = None
            continue

        if stripped.startswith("#"):
            command, _, value = stripped[1:].partition(" ")
            command = command.upper()
            value = value.strip()
            if command in {"BRANCHSTART", "BRANCHEND", "N", "E", "M"}:
                raise ValueError("Branching TJA charts are not supported by the manual editor.")
            try:
                if command == "BPMCHANGE":
                    bpm = float(value)
                    if not 20.0 < bpm < 500.0:
                        raise ValueError
                elif command == "MEASURE":
                    numerator, denominator = map(int, value.split("/", 1))
                    if not 1 <= numerator <= 32 or not 1 <= denominator <= 32:
                        raise ValueError
                elif command == "SCROLL":
                    scroll = float(value)
                    if not 0.0 < scroll <= 10.0:
                        raise ValueError
                elif command == "DELAY":
                    clock += float(value)
                    if clock < 0:
                        raise ValueError
            except ValueError as exc:
                raise ValueError(f"Invalid #{command} value on line {line_index + 1}.") from exc
            continue

        note_match = _NOTE_ROW.match(line)
        if not note_match:
            raise ValueError(f"Unsupported TJA chart data on line {line_index + 1}.")
        assert current_diff is not None
        prefix, note_text, suffix = note_match.groups()
        notes = [int(char) for char in note_text] if note_text else [0] * 16
        measure_idx = len(measures)
        duration = 240.0 * numerator / (denominator * bpm)
        measure = ChartMeasure(
            measure_idx=measure_idx,
            start_sec=clock,
            end_sec=clock + duration,
            bpm=bpm,
            numerator=numerator,
            denominator=denominator,
            scroll=scroll,
            notes=notes,
        )
        measures.append(measure)
        key = (current_diff, measure_idx)
        rows[key] = (line_index, prefix, suffix)
        original_notes[key] = list(notes)
        clock += duration

    if in_chart:
        raise ValueError("The TJA chart has a #START without a matching #END.")
    if not courses:
        raise ValueError("No supported TJA courses were found.")

    wave_filename = header.get("WAVE", "")
    audio_path = None
    if wave_filename:
        candidate = (path.parent / wave_filename).resolve()
        if candidate.is_relative_to(path.parent) and candidate.is_file():
            audio_path = candidate

    chart = TaikoChart(
        title=header.get("TITLE", path.stem),
        subtitle=header.get("SUBTITLE", "").removeprefix("--"),
        wave_filename=wave_filename,
        offset_sec=offset_sec,
        demo_start_sec=demo_start,
        base_bpm=base_bpm,
        genre=header.get("GENRE", ""),
        courses=courses,
    )
    return TjaDocument(
        path=path,
        chart=chart,
        lines=lines,
        newline=newline,
        trailing_newline=trailing_newline,
        encoding=encoding,
        rows=rows,
        original_notes=original_notes,
        balloon_rows=balloon_rows,
        course_starts=course_starts,
        course_spans=course_spans,
        level_lines=level_lines,
        offset_line=offset_line,
        first_course_line=first_course_line,
        audio_path=audio_path,
    )


def apply_tja_edits(document: TjaDocument, overrides: SongOverrides) -> TaikoChart:
    chart = document.chart.model_copy(deep=True)
    valid_codes = {note.value for note in TaikoNote}

    if overrides.barline_times:
        times = overrides.barline_times
        for course in chart.courses:
            if len(times) != len(course.measures) + 1:
                continue
            for idx, measure in enumerate(course.measures):
                start = float(times[idx])
                end = float(times[idx + 1])
                dur = end - start
                if dur < 0.12:
                    raise ValueError(f"Measure {idx} duration is too short after barline edit.")
                bpm = 240.0 * measure.numerator / (measure.denominator * dur)
                if not 20.0 < bpm < 500.0:
                    raise ValueError(f"Measure {idx} BPM ({bpm:.1f}) must stay between 20 and 500.")
                measure.start_sec = start
                measure.end_sec = end
                measure.bpm = bpm
        if chart.courses and chart.courses[0].measures:
            chart.offset_sec = max(0.0, chart.courses[0].measures[0].start_sec)

    delta_sec = (overrides.global_offset_ms or 0.0) / 1000.0
    if abs(delta_sec) > 1e-6:
        new_offset = max(0.0, chart.offset_sec + delta_sec)
        actual_delta = new_offset - chart.offset_sec
        chart.offset_sec = new_offset
        for course in chart.courses:
            for measure in course.measures:
                measure.start_sec = max(0.0, measure.start_sec + actual_delta)
                measure.end_sec = max(measure.start_sec + 0.12, measure.end_sec + actual_delta)

    if overrides.course_sources:
        by_diff = {c.difficulty: c for c in chart.courses}
        for target_diff, source_diff in overrides.course_sources.items():
            if target_diff == source_diff:
                continue
            src_course = by_diff.get(source_diff)
            if src_course is None:
                raise ValueError(f"Source course '{source_diff}' is not present in this TJA.")
            cloned = src_course.model_copy(deep=True)
            cloned.difficulty = target_diff
            by_diff[target_diff] = cloned
        chart.courses = [by_diff[d] for d in _COURSE_ORDER if d in by_diff]

    if overrides.disabled_courses:
        disabled = set(overrides.disabled_courses)
        chart.courses = [c for c in chart.courses if c.difficulty not in disabled]
        if not chart.courses:
            raise ValueError("At least one course must remain in the TJA chart.")

    for course in chart.courses:
        prefix = f"{course.difficulty}:"
        base_lengths = [len(measure.notes) for measure in course.measures]
        positioned: list[tuple[int, int, int, int]] = []
        direct: list[tuple[int, int, int]] = []
        for key, code in overrides.note_edits.items():
            if not key.startswith(prefix):
                continue
            if code not in valid_codes:
                raise ValueError(f"Invalid Taiko note code in edit '{key}'.")
            parts = key.split(":")
            if len(parts) != 3:
                raise ValueError(f"Invalid note edit key: {key}")
            try:
                measure_idx = int(parts[1])
            except ValueError as exc:
                raise ValueError(f"Invalid measure in note edit key: {key}") from exc
            if not 0 <= measure_idx < len(course.measures):
                raise ValueError(f"Note edit refers to missing measure: {key}")
            position = parts[2]
            if position.startswith("p"):
                try:
                    numerator, denominator = map(int, position[1:].split("/", 1))
                except ValueError as exc:
                    raise ValueError(f"Invalid fractional note edit key: {key}") from exc
                if denominator < 1 or denominator > 256 or not 0 <= numerator < denominator:
                    raise ValueError(f"Invalid fractional note position: {key}")
                positioned.append((measure_idx, numerator, denominator, code))
            else:
                try:
                    slot_idx = int(position)
                except ValueError as exc:
                    raise ValueError(f"Invalid note slot in edit key: {key}") from exc
                if not 0 <= slot_idx < base_lengths[measure_idx]:
                    raise ValueError(f"Note edit refers to missing slot: {key}")
                direct.append((measure_idx, slot_idx, code))

        denominators: dict[int, list[int]] = {}
        for measure_idx, _, denominator, _ in positioned:
            denominators.setdefault(measure_idx, []).append(denominator)
        for measure_idx, fractions in denominators.items():
            measure = course.measures[measure_idx]
            base_length = base_lengths[measure_idx]
            expanded_length = lcm(base_length, *fractions)
            if expanded_length > 4096:
                raise ValueError(f"Manual edit grid for measure {measure_idx} exceeds 4096 slots.")
            if expanded_length != base_length:
                expanded = [TaikoNote.REST.value] * expanded_length
                for slot_idx, note in enumerate(measure.notes):
                    expanded[slot_idx * (expanded_length // base_length)] = note
                measure.notes = expanded

        for measure_idx, slot_idx, code in direct:
            resolution = len(course.measures[measure_idx].notes) // base_lengths[measure_idx]
            course.measures[measure_idx].notes[slot_idx * resolution] = code
        for measure_idx, numerator, denominator, code in positioned:
            measure = course.measures[measure_idx]
            if len(measure.notes) % denominator:
                raise ValueError(f"Manual edit grid cannot represent note position in measure {measure_idx}.")
            measure.notes[numerator * (len(measure.notes) // denominator)] = code

        if course.difficulty in overrides.balloon_counts_by_course:
            course.balloon_counts = list(overrides.balloon_counts_by_course[course.difficulty])

        diff_ov = overrides.difficulty_overrides.get(course.difficulty)
        if diff_ov and diff_ov.star_rating is not None:
            course.level_stars = diff_ov.star_rating
    return chart


def _course_normalizes_scroll(measures: list[ChartMeasure]) -> float | None:
    if not measures:
        return None
    products = [m.bpm * m.scroll for m in measures]
    unique_scrolls = {round(m.scroll, 3) for m in measures}
    if (len(unique_scrolls) > 1 and max(products) - min(products) < 2.0) or all(
        abs(p - 101.343) < 2.0 for p in products
    ):
        return sum(products) / len(products)
    return None


def _render_new_or_cloned_course_lines(course: ChartDifficulty, chart: TaikoChart) -> list[str]:
    norm_speed = _course_normalizes_scroll(course.measures)
    base_scroll = course.measures[0].scroll if course.measures else 0.5
    return render_course_tja(
        course=course,
        initial_bpm=chart.base_bpm,
        base_scroll=base_scroll,
        bpm_deadzone=0.001,
        normalize_scroll=norm_speed is not None,
        target_highway_speed=norm_speed if norm_speed is not None else 101.343,
    )


def render_tja_edits(
    document: TjaDocument,
    chart: TaikoChart,
    overrides: SongOverrides | None = None,
) -> bytes:
    replacements: dict[int, str] = {}
    deletions: set[int] = set()
    before_inserts: dict[int, list[str]] = {}
    appends: list[str] = []

    if abs(chart.offset_sec - document.chart.offset_sec) >= 0.0005:
        tja_offset = -float(chart.offset_sec)
        if round(tja_offset, 3) == 0.0:
            tja_offset = 0.0
        if document.offset_line is not None:
            match = _OFFSET_ROW.match(document.lines[document.offset_line])
            prefix = match.group(1) if match else "OFFSET:"
            replacements[document.offset_line] = f"{prefix}{tja_offset:.3f}"
        else:
            before_inserts.setdefault(document.first_course_line, []).append(f"OFFSET:{tja_offset:.3f}")

    orig_courses = {c.difficulty: c for c in document.chart.courses}
    active_courses = {c.difficulty: c for c in chart.courses}
    cloned_targets = {
        dst
        for dst, src in (overrides.course_sources.items() if overrides else ())
        if dst != src
    }

    for diff, (span_start, span_end) in document.course_spans.items():
        if diff not in active_courses:
            for idx in range(span_start, span_end + 1):
                deletions.add(idx)
            if span_end + 1 < len(document.lines) and not document.lines[span_end + 1].strip():
                deletions.add(span_end + 1)

    for course in chart.courses:
        diff = course.difficulty
        orig_course = orig_courses.get(diff)
        is_new_or_cloned = (diff not in document.course_spans) or (diff in cloned_targets)

        if is_new_or_cloned:
            block_lines = _render_new_or_cloned_course_lines(course, chart)
            if diff in document.course_spans:
                span_start, span_end = document.course_spans[diff]
                for idx in range(span_start, span_end + 1):
                    deletions.add(idx)
                before_inserts.setdefault(span_start, []).extend(block_lines)
            else:
                if appends or (document.lines and document.lines[-1].strip()):
                    appends.append("")
                appends.extend(block_lines)
            continue

        if orig_course and course.level_stars != orig_course.level_stars:
            lvl_idx = document.level_lines.get(diff)
            if lvl_idx is not None:
                match = _LEVEL_ROW.match(document.lines[lvl_idx])
                prefix = match.group(1) if match else "LEVEL:"
                replacements[lvl_idx] = f"{prefix}{course.level_stars}"
            else:
                span_start = document.course_spans[diff][0]
                before_inserts.setdefault(span_start + 1, []).append(f"LEVEL:{course.level_stars}")

        for measure in course.measures:
            key = (diff, measure.measure_idx)
            if key not in document.rows or measure.notes == document.original_notes[key]:
                continue
            line_index, prefix, suffix = document.rows[key]
            replacements[line_index] = f"{prefix}{''.join(str(note) for note in measure.notes)}{suffix}"

        original_balloon = document.balloon_rows.get(diff, (None, -1, []))
        line_index, start_line, original_counts = original_balloon
        if course.balloon_counts != original_counts:
            value = ",".join(str(count) for count in course.balloon_counts)
            if line_index is not None:
                match = _BALLOON_ROW.match(document.lines[line_index])
                prefix = match.group(1) if match else "BALLOON:"
                replacements[line_index] = prefix + value
            elif course.balloon_counts:
                before_inserts.setdefault(start_line, []).append("BALLOON:" + value)

        if orig_course and len(orig_course.measures) == len(course.measures):
            timing_changed = any(
                abs(m.bpm - om.bpm) >= 0.002
                for m, om in zip(course.measures, orig_course.measures)
            )
            if timing_changed:
                target_speed = _course_normalizes_scroll(orig_course.measures)
                active_bpm = chart.base_bpm
                active_scroll = orig_course.measures[0].scroll if orig_course.measures else 0.5
                prev_boundary = document.course_starts.get(diff, -1) + 1

                for measure in course.measures:
                    key = (diff, measure.measure_idx)
                    if key not in document.rows:
                        continue
                    row_line = document.rows[key][0]
                    slice_bpm_lines = [
                        idx for idx in range(prev_boundary, row_line)
                        if document.lines[idx].strip().upper().startswith("#BPMCHANGE")
                    ]
                    slice_scroll_lines = [
                        idx for idx in range(prev_boundary, row_line)
                        if document.lines[idx].strip().upper().startswith("#SCROLL")
                    ]
                    prev_boundary = row_line + 1

                    desired_bpm = round(measure.bpm, 3)
                    if abs(desired_bpm - active_bpm) >= 0.002:
                        if slice_bpm_lines:
                            for idx in slice_bpm_lines[:-1]:
                                deletions.add(idx)
                            replacements[slice_bpm_lines[-1]] = f"#BPMCHANGE {desired_bpm:.3f}"
                        else:
                            before_inserts.setdefault(row_line, []).append(f"#BPMCHANGE {desired_bpm:.3f}")
                        active_bpm = desired_bpm
                    elif slice_bpm_lines:
                        for idx in slice_bpm_lines:
                            deletions.add(idx)

                    if target_speed is not None:
                        desired_scroll = max(0.05, min(10.0, round(target_speed / active_bpm, 3)))
                        if abs(desired_scroll - active_scroll) >= 0.001:
                            if slice_scroll_lines:
                                for idx in slice_scroll_lines[:-1]:
                                    deletions.add(idx)
                                replacements[slice_scroll_lines[-1]] = f"#SCROLL {desired_scroll:.3f}"
                            else:
                                before_inserts.setdefault(row_line, []).append(f"#SCROLL {desired_scroll:.3f}")
                            active_scroll = desired_scroll
                        elif slice_scroll_lines and measure.measure_idx > 0:
                            for idx in slice_scroll_lines:
                                deletions.add(idx)

    out_lines: list[str] = []
    for line_idx, orig_line in enumerate(document.lines):
        if line_idx in before_inserts:
            out_lines.extend(before_inserts[line_idx])
        if line_idx in deletions:
            continue
        out_lines.append(replacements.get(line_idx, orig_line))
    if appends:
        out_lines.extend(appends)

    text = document.newline.join(out_lines)
    if document.trailing_newline or appends:
        text += document.newline
    return text.encode(document.encoding)


def save_tja_edits(
    document: TjaDocument,
    chart: TaikoChart,
    overrides: SongOverrides | None = None,
) -> Path:
    backup_path = document.path.with_suffix(document.path.suffix + ".bak")
    if not backup_path.exists():
        shutil.copy2(document.path, backup_path)

    temporary_path = document.path.with_name(document.path.name + ".tmp")
    try:
        temporary_path.write_bytes(render_tja_edits(document, chart, overrides=overrides))
        os.replace(temporary_path, document.path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise
    return document.path