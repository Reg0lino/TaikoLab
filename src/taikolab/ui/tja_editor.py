"""Import and preserve existing TJA files while applying manual note edits."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from math import lcm
from pathlib import Path

from taikolab.schemas import ChartDifficulty, ChartMeasure, SongOverrides, TaikoChart, TaikoNote

_COURSES = {
    "easy": "easy",
    "normal": "normal",
    "hard": "hard",
    "oni": "oni",
    "edit": "ura",
}
_COURSE_NAMES = {value: key.title() for key, value in _COURSES.items()}
_NOTE_ROW = re.compile(r"^(\s*)([0-8]*)(\s*,.*)$")
_BALLOON_ROW = re.compile(r"^(\s*BALLOON\s*:\s*).*$", re.IGNORECASE)


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
    for line in lines:
        match = re.match(r"^\s*([A-Za-z_]+)\s*:(.*)$", line)
        if match:
            key = match.group(1).upper()
            if key == "COURSE":
                break
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
    courses: list[ChartDifficulty] = []
    current_name: str | None = None
    current_diff: str | None = None
    current_level = 5
    current_balloons: list[int] = []
    balloon_line: int | None = None
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
            elif key == "LEVEL" and current_diff:
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
            in_chart = False
            current_diff = None
            current_name = None
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
        audio_path=audio_path,
    )


def apply_tja_edits(document: TjaDocument, overrides: SongOverrides) -> TaikoChart:
    chart = document.chart.model_copy(deep=True)
    valid_codes = {note.value for note in TaikoNote}
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
    return chart


def render_tja_edits(document: TjaDocument, chart: TaikoChart) -> bytes:
    lines = list(document.lines)
    inserts: list[tuple[int, str]] = []
    for course in chart.courses:
        for measure in course.measures:
            key = (course.difficulty, measure.measure_idx)
            if key not in document.rows or measure.notes == document.original_notes[key]:
                continue
            line_index, prefix, suffix = document.rows[key]
            lines[line_index] = f"{prefix}{''.join(str(note) for note in measure.notes)}{suffix}"

        original_balloon = document.balloon_rows.get(
            course.difficulty, (None, -1, [])
        )
        line_index, start_line, original_counts = original_balloon
        if course.balloon_counts == original_counts:
            continue
        value = ",".join(str(count) for count in course.balloon_counts)
        if line_index is not None:
            match = _BALLOON_ROW.match(lines[line_index])
            prefix = match.group(1) if match else "BALLOON:"
            lines[line_index] = prefix + value
        elif course.balloon_counts:
            inserts.append((start_line, "BALLOON:" + value))

    for line_index, line in sorted(inserts, reverse=True):
        lines.insert(line_index, line)
    text = document.newline.join(lines)
    if document.trailing_newline:
        text += document.newline
    return text.encode(document.encoding)


def save_tja_edits(document: TjaDocument, chart: TaikoChart) -> Path:
    backup_path = document.path.with_suffix(document.path.suffix + ".bak")
    if not backup_path.exists():
        shutil.copy2(document.path, backup_path)

    temporary_path = document.path.with_name(document.path.name + ".tmp")
    try:
        temporary_path.write_bytes(render_tja_edits(document, chart))
        os.replace(temporary_path, document.path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise
    return document.path
