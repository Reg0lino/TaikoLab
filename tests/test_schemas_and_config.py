import pytest
from pydantic import ValidationError

from taikolab.config import load_config
from taikolab.schemas import (
    BeatEvent,
    ChartDifficulty,
    ChartMeasure,
    DrumHit,
    OnsetEvent,
    SectionOverride,
    SongAnalysis,
    SongOverrides,
    TaikoChart,
    TaikoNote,
)


@pytest.mark.parametrize("preset_name", ["punk_gallop", "mathcore", "powerviolence"])
def test_load_all_presets(preset_name: str):
    cfg = load_config(preset_name=preset_name)
    assert cfg.preset.name == preset_name
    assert cfg.hori.max_nps_oni > cfg.hori.max_nps_normal
    assert cfg.hori.max_nps_for("normal") == cfg.hori.max_nps_normal


def test_song_analysis_json_roundtrip():
    analysis = SongAnalysis(
        song_id="byob",
        title="B.Y.O.B.",
        artist="System of a Down",
        audio_path="audio.ogg",
        duration_sec=255.0,
        beats=[
            BeatEvent(time_sec=0.5, is_downbeat=True),
            BeatEvent(time_sec=1.08, is_downbeat=False),
        ],
        drum_hits=[
            DrumHit(time_sec=0.5, drum_class="kick", velocity=0.95),
            DrumHit(time_sec=0.79, drum_class="snare", velocity=0.90),
        ],
        vocal_events=[OnsetEvent(time_sec=12.4, strength=0.8)],
    )
    raw = analysis.model_dump_json()
    restored = SongAnalysis.model_validate_json(raw)
    assert restored == analysis


def test_overrides_validation_rejects_inverted_section():
    with pytest.raises(ValidationError):
        SectionOverride(start_sec=10.0, end_sec=8.0)

    ov = SongOverrides(
        song_id="california_uber_alles",
        sections=[
            SectionOverride(
                start_sec=0.0,
                end_sec=24.5,
                label="Verse Vocals",
                follow_mode="vocals",
            )
        ],
    )
    assert ov.sections[0].follow_mode == "vocals"


def test_chart_measure_validates_note_codes():
    m = ChartMeasure(
        measure_idx=0,
        start_sec=0.0,
        end_sec=2.0,
        bpm=120.0,
        numerator=4,
        denominator=4,
        notes=[TaikoNote.DON, 0, TaikoNote.KAT, 0] * 4,
    )
    chart = TaikoChart(
        title="Test Song",
        wave_filename="audio.ogg",
        base_bpm=120.0,
        courses=[ChartDifficulty(difficulty="oni", level_stars=8, measures=[m])],
    )
    assert len(chart.courses[0].measures[0].notes) == 16

    with pytest.raises(ValidationError):
        ChartMeasure(
            measure_idx=1,
            start_sec=2.0,
            end_sec=4.0,
            bpm=120.0,
            notes=[99, 0, 0, 0],
        )