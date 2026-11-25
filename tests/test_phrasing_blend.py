"""Tests for intelligent phrasing blend / alternation modes (2-bar, 4-bar, adaptive & fallback)."""

import pytest
from taikolab.arranger.difficulty import build_course
from taikolab.arranger.quantizer import MeasureSlot
from taikolab.arranger.section_builder import build_measure_plans
from taikolab.config import load_config
from taikolab.schemas import (
    BeatEvent,
    DrumHit,
    EnergyPoint,
    OnsetEvent,
    SectionOverride,
    SongAnalysis,
    SongOverrides,
    TaikoNote,
    TempoRegion,
)


def _make_8bar_analysis() -> tuple[SongAnalysis, list[MeasureSlot]]:
    slots = []
    beats = []
    drum_hits = []
    vocal_events = []
    guitar_events = []
    energy_curve = []

    for m in range(8):
        m_start = m * 2.0
        m_end = (m + 1) * 2.0
        slots.append(
            MeasureSlot(
                measure_idx=m,
                start_sec=m_start,
                end_sec=m_end,
                bpm=120.0,
                numerator=4,
                denominator=4,
                grid_size=16,
            )
        )
        for b in range(4):
            t = m_start + b * 0.5
            beats.append(BeatEvent(time_sec=t, is_downbeat=(b == 0)))
            drum_hits.append(DrumHit(time_sec=t, drum_class="kick" if b in (0, 2) else "snare", velocity=0.85))

        if m in (0, 1, 4, 5):
            vocal_events.append(OnsetEvent(time_sec=m_start + 0.25, strength=0.75))
            vocal_events.append(OnsetEvent(time_sec=m_start + 0.75, strength=0.80))
            if m != 1:
                vocal_events.append(OnsetEvent(time_sec=m_start + 1.25, strength=0.70))

        if m in (2, 3, 6, 7):
            guitar_events.append(OnsetEvent(time_sec=m_start + 0.25, strength=0.82))
            guitar_events.append(OnsetEvent(time_sec=m_start + 1.25, strength=0.78))

        energy_curve.append(EnergyPoint(time_sec=m_start + 1.0, drums_rms=0.6, vocals_rms=0.5, guitar_rms=0.5))

    analysis = SongAnalysis(
        song_id="test_phrasing",
        title="Test Phrasing",
        audio_path="test.wav",
        duration_sec=16.0,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=16.0, bpm=120.0)],
        drum_hits=drum_hits,
        vocal_events=vocal_events,
        guitar_events=guitar_events,
        energy_curve=energy_curve,
    )
    return analysis, slots


def test_alt_2bar_drums_vocals():
    analysis, slots = _make_8bar_analysis()
    cfg = load_config("punk_gallop")

    overrides = SongOverrides(
        song_id="test_phrasing",
        sections=[
            SectionOverride(
                start_sec=0.0,
                end_sec=16.0,
                label="Verse",
                follow_mode="drums_vocals",
                blend_style="alt_2bar",
                phrasing_lead="vocals",
            )
        ],
    )

    plans = build_measure_plans(slots, analysis, "hard", cfg, overrides)

    assert plans[0].follow_mode == "vocals"
    assert plans[1].follow_mode == "vocals"
    assert plans[2].follow_mode == "drums"
    assert plans[3].follow_mode == "drums"
    assert plans[4].follow_mode == "vocals"
    assert plans[5].follow_mode == "vocals"
    assert plans[6].follow_mode == "drums"
    assert plans[7].follow_mode == "drums"

    course = build_course(analysis, slots, "hard", cfg, overrides)
    assert len(course.measures) == 8


def test_alt_4bar_hybrid():
    analysis, slots = _make_8bar_analysis()
    cfg = load_config("punk_gallop")

    overrides = SongOverrides(
        song_id="test_phrasing",
        sections=[
            SectionOverride(
                start_sec=0.0,
                end_sec=16.0,
                label="Solo",
                follow_mode="hybrid",
                blend_style="alt_4bar",
                phrasing_lead="instruments",
            )
        ],
    )

    plans = build_measure_plans(slots, analysis, "hard", cfg, overrides)

    assert plans[0].follow_mode == "drums"
    assert plans[1].follow_mode == "drums"
    assert plans[2].follow_mode == "guitar"
    assert plans[3].follow_mode == "guitar"

    for m in range(4, 8):
        assert plans[m].follow_mode == "drums"


def test_vocal_natural_breathing_room():
    analysis, slots = _make_8bar_analysis()
    cfg = load_config("punk_gallop")

    overrides = SongOverrides(
        song_id="test_phrasing",
        sections=[
            SectionOverride(
                start_sec=0.0,
                end_sec=16.0,
                label="Verse",
                follow_mode="drums_vocals",
                blend_style="alt_2bar",
                phrasing_lead="vocals",
            )
        ],
    )

    course = build_course(analysis, slots, "hard", cfg, overrides)
    bar1_notes = course.measures[1].notes
    active_notes = [n for n in bar1_notes if n in (TaikoNote.DON, TaikoNote.KAT, TaikoNote.BIG_DON, TaikoNote.BIG_KAT)]

    assert len(active_notes) <= 3


def test_vocal_silent_bar_fallback():
    analysis, slots = _make_8bar_analysis()
    analysis.vocal_events = [v for v in analysis.vocal_events if v.time_sec >= 2.0]
    cfg = load_config("punk_gallop")

    overrides = SongOverrides(
        song_id="test_phrasing",
        sections=[
            SectionOverride(
                start_sec=0.0,
                end_sec=16.0,
                label="Verse",
                follow_mode="drums_vocals",
                blend_style="alt_2bar",
                phrasing_lead="vocals",
            )
        ],
    )

    plans = build_measure_plans(slots, analysis, "hard", cfg, overrides)
    assert plans[0].follow_mode == "drums"
    assert plans[1].follow_mode == "vocals"


def test_lead_priority_config():
    analysis, slots = _make_8bar_analysis()
    cfg = load_config("punk_gallop")

    overrides = SongOverrides(
        song_id="test_phrasing",
        sections=[
            SectionOverride(
                start_sec=0.0,
                end_sec=16.0,
                label="Verse",
                follow_mode="drums_vocals",
                blend_style="alt_2bar",
                phrasing_lead="drums",
            )
        ],
    )

    plans = build_measure_plans(slots, analysis, "hard", cfg, overrides)
    assert plans[0].follow_mode == "drums"
    assert plans[1].follow_mode == "drums"
    assert plans[2].follow_mode == "drums"
    assert plans[4].follow_mode == "drums"


def test_adaptive_phrase_switching():
    analysis, slots = _make_8bar_analysis()
    cfg = load_config("punk_gallop")

    overrides = SongOverrides(
        song_id="test_phrasing",
        sections=[
            SectionOverride(
                start_sec=0.0,
                end_sec=16.0,
                label="Verse",
                follow_mode="drums_vocals",
                blend_style="adaptive",
                phrasing_lead="vocals",
            )
        ],
    )

    plans = build_measure_plans(slots, analysis, "hard", cfg, overrides)
    assert plans[0].follow_mode == "vocals"
    assert plans[1].follow_mode == "vocals"
    assert plans[2].follow_mode == "drums"
    assert plans[3].follow_mode == "drums"


def test_backward_compatibility_simultaneous():
    analysis, slots = _make_8bar_analysis()
    cfg = load_config("punk_gallop")

    overrides = SongOverrides(
        song_id="test_phrasing",
        section_phrase_variety=0.0,
        sections=[
            SectionOverride(
                start_sec=0.0,
                end_sec=16.0,
                label="Verse",
                follow_mode="drums_vocals",
                blend_style="simultaneous",
            )
        ],
    )

    plans = build_measure_plans(slots, analysis, "hard", cfg, overrides)
    for p in plans:
        assert p.follow_mode == "drums_vocals"
