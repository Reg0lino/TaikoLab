"""Tests for fail-proof project save, auto-healing rolls/balloons, and global offset lock."""

from __future__ import annotations

import numpy as np

from taikolab.ingest.tab_sync import _find_global_drum_offset
from taikolab.schemas import ChartDifficulty, ChartMeasure, DifficultyLevel, DrumHit, SongOverrides, TaikoChart, TaikoNote
from taikolab.ui.server import _auto_heal_chart_rolls_and_balloons


def test_find_global_drum_offset_recovers_13_second_album_intro_shift() -> None:
    # MIDI drums start at 15.0s..60.0s
    midi_drums = [
        DrumHit(time_sec=round(t, 4), drum_class="kick" if i % 2 == 0 else "snare", velocity=0.9, source="tab")
        for i, t in enumerate(np.arange(15.0, 60.0, 0.40))
    ]
    # Real audio has 13.24s extra album intro padding (first drum at 28.24s)
    real_drums = [
        DrumHit(time_sec=round(h.time_sec + 13.24, 4), drum_class=h.drum_class, velocity=0.9, source="adtof")
        for h in midi_drums
    ]

    offset, matches, total = _find_global_drum_offset(midi_drums, real_drums, search_range_sec=45.0)
    assert abs(offset - 13.24) <= 0.010
    assert matches >= int(total * 0.95)


def test_auto_heal_chart_rolls_and_balloons_prevents_save_crashes() -> None:
    # Course has 2 balloons, but balloon_counts only has 1 count, and measure has a note inside a roll
    measure = ChartMeasure(
        measure_idx=0,
        start_sec=0.0,
        end_sec=2.0,
        bpm=120.0,
        notes=[7, 1, 2, 8, 7, 8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    )
    course = ChartDifficulty(
        difficulty="oni",
        measures=[measure],
        balloon_counts=[15],  # Mismatched length (1 vs 2)
    )
    chart = TaikoChart(
        title="Test",
        wave_filename="audio.wav",
        base_bpm=120.0,
        courses=[course],
    )
    overrides = SongOverrides(song_id="test-save", balloon_counts_by_course={"oni": [15]})

    _auto_heal_chart_rolls_and_balloons(chart, overrides)

    # Balloon counts auto-healed to length 2
    assert len(course.balloon_counts) == 2
    assert course.balloon_counts[0] == 15
    assert course.balloon_counts[1] == 10
    assert overrides.balloon_counts_by_course["oni"] == [15, 10]
    # Stray notes inside roll (slots 1 and 2) wiped to REST
    assert measure.notes[1] == TaikoNote.REST
    assert measure.notes[2] == TaikoNote.REST