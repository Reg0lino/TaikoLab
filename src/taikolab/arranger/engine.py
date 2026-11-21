"""Main pure-Python TaikoLab Arranger entry point."""

from __future__ import annotations

import statistics

from taikolab.arranger.difficulty import build_course
from taikolab.arranger.quantizer import build_base_and_warped_slots
from taikolab.config import AppConfig
from taikolab.schemas import DifficultyLevel, SongAnalysis, SongOverrides, TaikoChart


def arrange_song(
    analysis: SongAnalysis,
    cfg: AppConfig,
    overrides: SongOverrides | None = None,
    difficulties: tuple[DifficultyLevel, ...] = ("easy", "normal", "hard", "oni"),
) -> TaikoChart:
    """Convert cached SongAnalysis + optional overrides into a complete TaikoChart."""
    base_slots, slots = build_base_and_warped_slots(analysis, overrides)
    if not slots:
        raise ValueError(f"Could not generate any measures for song '{analysis.song_id}'.")

    disabled = set(overrides.disabled_courses) if overrides else set()
    active_difficulties = [diff for diff in difficulties if diff not in disabled]
    if not active_difficulties:
        raise ValueError("At least one course must remain enabled.")

    courses = [
        build_course(
            analysis,
            slots,
            diff,
            cfg,
            overrides,
            base_slots=base_slots,
            rule_difficulty=overrides.course_sources.get(diff, diff) if overrides else diff,
        )
        for diff in active_difficulties
    ]

    bpms = [s.bpm for s in slots]
    base_bpm = round(statistics.median(bpms), 3) if bpms else 140.0

    offset_ms = cfg.settings.global_audio_offset_ms
    if overrides and overrides.global_offset_ms is not None:
        offset_ms = overrides.global_offset_ms

    first_bar_sec = slots[0].start_sec
    total_offset_sec = first_bar_sec + (offset_ms / 1000.0)

    title = (overrides.title if overrides and overrides.title else None) or analysis.title
    artist = (overrides.artist if overrides and overrides.artist else None) or analysis.artist

    return TaikoChart(
        title=title,
        subtitle=artist,
        wave_filename=analysis.audio_path,
        offset_sec=total_offset_sec,
        demo_start_sec=round(min(analysis.duration_sec * 0.25, 30.0), 2),
        base_bpm=base_bpm,
        genre=cfg.preset.name.replace("_", " ").title(),
        courses=courses,
    )