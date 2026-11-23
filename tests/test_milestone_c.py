"""Milestone C tests: UI-driven overrides (no blast folding by default), ride modes & GP balloons."""

from __future__ import annotations

from taikolab.arranger.engine import arrange_song
from taikolab.config import apply_chart_controls_to_config, load_config
from taikolab.schemas import (
    BeatEvent,
    DrumHit,
    GpHints,
    GpSectionMarker,
    SongAnalysis,
    SongOverrides,
    TaikoNote,
    TempoRegion,
)


def test_fold_blasts_disabled_via_overrides_keeps_playable_fast_notes() -> None:
    cfg = load_config("taiko_auto")
    beats = []
    for b in range(4):
        beats.append(BeatEvent(time_sec=b * 1.1, is_downbeat=True))
        beats.append(BeatEvent(time_sec=b * 1.1 + 0.275, is_downbeat=False))
        beats.append(BeatEvent(time_sec=b * 1.1 + 0.55, is_downbeat=False))
        beats.append(BeatEvent(time_sec=b * 1.1 + 0.825, is_downbeat=False))

    drums = [
        DrumHit(time_sec=round(t, 4), drum_class="kick" if i % 2 == 0 else "snare", velocity=0.9, source="tab")
        for i, t in enumerate([0.05 + k * 0.055 for k in range(45)])
        if t < 4.3
    ]
    analysis = SongAnalysis(
        song_id="fast-no-fold",
        title="Fast",
        audio_path="audio.wav",
        duration_sec=4.5,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=4.5, bpm=218.0)],
        drum_hits=drums,
    )
    ov = SongOverrides(song_id="fast-no-fold", fold_blasts_to_rolls=False)
    apply_chart_controls_to_config(cfg, ov)
    chart = arrange_song(analysis, cfg, overrides=ov)
    oni = next(c for c in chart.courses if c.difficulty == "oni")
    assert not any(n == TaikoNote.DRUMROLL for m in oni.measures for n in m.notes)


def test_gp_tremolo_and_chorus_drop_trigger_intelligent_balloon_and_ride_ignore() -> None:
    cfg = load_config("taiko_auto")
    beats = []
    for b in range(8):
        beats.append(BeatEvent(time_sec=b * 2.0, is_downbeat=True))
        beats.append(BeatEvent(time_sec=b * 2.0 + 0.5, is_downbeat=False))
        beats.append(BeatEvent(time_sec=b * 2.0 + 1.0, is_downbeat=False))
        beats.append(BeatEvent(time_sec=b * 2.0 + 1.5, is_downbeat=False))

    drums = []
    for k, dt in enumerate([0.0, 0.14, 0.28, 0.42, 0.56]):
        drums.append(DrumHit(time_sec=6.8 + dt, drum_class="snare" if k < 3 else "tom", velocity=0.92, source="tab", pitch=38 if k < 3 else 47))
    drums.append(DrumHit(time_sec=8.0, drum_class="crash", velocity=0.95, source="tab"))
    drums.append(DrumHit(time_sec=10.5, drum_class="ride", velocity=0.90, source="tab"))

    analysis = SongAnalysis(
        song_id="gp-balloon",
        title="GP Balloon",
        audio_path="audio.wav",
        duration_sec=16.0,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=16.0, bpm=120.0)],
        drum_hits=drums,
        gp_hints=GpHints(
            sections=[GpSectionMarker(bar_idx=4, label="Chorus", role="chorus", time_sec=8.0)],
            tremolo_bars=[3],
        ),
    )
    ov = SongOverrides(
        song_id="gp-balloon",
        fold_blasts_to_rolls=False,
        ride_cymbal_mode="ignore",
        fill_rolls_by_course={"hard": 3, "oni": 3},
    )
    apply_chart_controls_to_config(cfg, ov)
    chart = arrange_song(analysis, cfg, overrides=ov)
    hard = next(c for c in chart.courses if c.difficulty == "hard")
    assert any(n == TaikoNote.BALLOON for n in hard.measures[3].notes)
    assert all(n == TaikoNote.REST for n in hard.measures[5].notes)

def test_kick_plus_crash_at_measure_start_favors_big_don_and_triples_strip_big_notes() -> None:
    from taikolab.arranger.groove_triples import apply_groove_triples
    from taikolab.arranger.mapper_drums import PlacedCandidate, map_drum_hits
    from taikolab.arranger.quantizer import MeasureSlot
    from taikolab.arranger.section_builder import MeasurePlan
    from taikolab.schemas import ChartMeasure

    cfg = load_config("taiko_auto")
    slots = [MeasureSlot(measure_idx=0, start_sec=0.0, end_sec=2.0, bpm=120.0, numerator=4, denominator=4, grid_size=16)]
    plans = [MeasurePlan(measure_idx=0, follow_mode="drums", difficulty="hard")]
    hits = [
        DrumHit(time_sec=0.0, drum_class="kick", velocity=0.95, source="tab"),
        DrumHit(time_sec=0.0, drum_class="crash", velocity=0.95, source="tab"),
    ]
    placed = map_drum_hits(hits, slots, plans, cfg)
    assert placed[(0, 0)].note_type == TaikoNote.BIG_DON

    # Triple pocket containing BIG_DON / BIG_KAT must demote big notes inside the triple
    measures = [
        ChartMeasure(
            measure_idx=i,
            start_sec=i * 2.0,
            end_sec=(i + 1) * 2.0,
            bpm=120.0,
            notes=[3 if i == 1 else 1, 0, 0, 0, 2, 0, 0, 0, 1, 0, 0, 0, 4 if i == 1 else 2, 0, 0, 0],
        )
        for i in range(4)
    ]
    drum_cands = {
        (i, s): PlacedCandidate(measure_idx=i, slot_idx=s, time_sec=i * 2.0 + s * 0.125, note_type=1 if s % 8 == 0 else 2, priority=0.9, source="drum:kick")
        for i in range(4)
        for s in (0, 4, 8, 12)
    }
    triple_plans = [MeasurePlan(measure_idx=i, follow_mode="drums", difficulty="hard", groove_triple_density=1.0) for i in range(4)]
    out = apply_groove_triples(measures, drum_cands, triple_plans, "hard")
    for m in out:
        if m.notes[1] != 0 or m.notes[2] != 0 or m.notes[9] != 0 or m.notes[10] != 0:
            assert TaikoNote.BIG_DON not in m.notes and TaikoNote.BIG_KAT not in m.notes