from pathlib import Path

import mido

from taikolab.arranger.engine import arrange_song
from taikolab.config import load_config
from taikolab.exporters.diagnostics import analyze_course
from taikolab.ingest.tab_sync import _parse_midi_unified, _split_double_time_bars
from taikolab.schemas import BeatEvent, DrumHit, SongAnalysis, StemPaths, TempoRegion


def test_unified_mido_parser_skips_shaker_and_splits_double_time(tmp_path: Path):
    mid = mido.MidiFile(ticks_per_beat=480)

    # Track 0: tempo & vocal
    tr0 = mido.MidiTrack()
    tr0.name = "Vocals"
    tr0.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(103.0), time=0))
    tr0.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    tr0.append(mido.Message("note_on", channel=0, note=64, velocity=100, time=0))
    tr0.append(mido.Message("note_off", channel=0, note=64, velocity=0, time=240))
    mid.tracks.append(tr0)

    # Track 1: Drums with fast 32nd hits
    tr1 = mido.MidiTrack()
    tr1.name = "Travis | Drums"
    for i in range(16):
        tr1.append(mido.Message("note_on", channel=9, note=36 if i % 2 == 0 else 38, velocity=100, time=0 if i == 0 else 60))
        tr1.append(mido.Message("note_off", channel=9, note=36 if i % 2 == 0 else 38, velocity=0, time=10))
    mid.tracks.append(tr1)

    # Track 2: Aux Shaker on channel 9 -> should be ignored!
    tr2 = mido.MidiTrack()
    tr2.name = "Shaker"
    tr2.append(mido.Message("note_on", channel=9, note=70, velocity=90, time=0))
    tr2.append(mido.Message("note_off", channel=9, note=70, velocity=0, time=120))
    mid.tracks.append(tr2)

    mid_path = tmp_path / "test_unified.mid"
    mid.save(str(mid_path))

    bars, drums, vocals, _, _ = _parse_midi_unified(mid_path)
    assert len(drums) == 16  # Shaker excluded!
    assert len(vocals) == 1
    split_bars = _split_double_time_bars(bars, drums)
    assert split_bars[0][2] > 190.0


def test_four_difficulty_ladder_scales_cleanly_for_hori():
    cfg = load_config("punk_gallop")
    # 200 BPM 4/4 (beat = 0.30s) with 16th drum hits
    beats = [BeatEvent(time_sec=i * 0.30, is_downbeat=(i % 4 == 0)) for i in range(32)]
    drums = [
        DrumHit(time_sec=i * 0.075, drum_class="kick" if i % 2 == 0 else "snare", velocity=0.90)
        for i in range(120)
    ]
    analysis = SongAnalysis(
        song_id="punk_ladder",
        title="Punk Ladder",
        audio_path="audio.wav",
        duration_sec=10.0,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=10.0, bpm=200.0, numerator=4, denominator=4)],
        drum_hits=drums,
    )
    chart = arrange_song(analysis, cfg)
    counts = {c.difficulty: analyze_course(c).total_notes for c in chart.courses}
    assert counts["easy"] < counts["normal"] < counts["hard"] < counts["oni"]


def test_isolated_drum_warp_tracks_smooth_drift():
    import numpy as np

    from taikolab.ingest.tab_sync import _estimate_isolated_drum_warp

    # Simulate 60s of 160 BPM beats where real audio drifts from +0.50s to +0.74s
    m_ks = np.arange(0.0, 60.0, 0.375)
    true_lag = 0.50 + 0.004 * m_ks
    r_ks = m_ks + true_lag

    km, ka, rate = _estimate_isolated_drum_warp(m_ks, r_ks, duration_sec=62.0, first_note_t=0.0, first_aud_transient=0.50)
    assert rate >= 0.90
    warped = np.interp(m_ks, km, ka)
    assert float(np.median(np.abs(warped - r_ks))) < 0.015


def test_drum_alignment_rejects_a_phase_slipped_dtw_warp():
    import numpy as np

    from taikolab.ingest.tab_sync import _score_drum_warp, _select_drum_alignment_warp

    midi_drums = [
        DrumHit(
            time_sec=float(i * 0.5),
            drum_class="kick" if i % 2 == 0 else "snare",
            velocity=0.9,
        )
        for i in range(160)
    ]
    real_drums = [
        DrumHit(
            time_sec=0.2 + 1.001 * hit.time_sec,
            drum_class=hit.drum_class,
            velocity=0.9,
        )
        for hit in midi_drums
    ]
    dtw_knots_m = np.array([0.0, 20.0, 40.0, 80.0])
    dtw_knots_a = np.array([0.2, 20.2, 40.6, 81.0])

    old_matches, total = _score_drum_warp(midi_drums, real_drums, dtw_knots_m, dtw_knots_a)
    knots_m, knots_a, new_matches, selected_total, rate = _select_drum_alignment_warp(
        midi_drums,
        real_drums,
        82.0,
        dtw_knots_m,
        dtw_knots_a,
    )

    assert rate is not None
    assert selected_total == total
    assert new_matches >= old_matches + 12
    assert abs(rate - 1.001) < 0.001
    expected_times = np.array([0.2, 40.24, 79.7795])
    actual_times = np.interp([0.0, 40.0, 79.5], knots_m, knots_a)
    assert float(np.median(np.abs(actual_times - expected_times))) < 0.015


def test_short_fast_fill_does_not_double_the_whole_measure():
    from taikolab.ingest.tab_sync import _split_double_time_bars

    bars = [(0.0, 2.5, 96.0, 4, 4)]
    fill_hits = [
        DrumHit(time_sec=1.30 + i * 0.08, drum_class="snare", velocity=0.9)
        for i in range(10)
    ]

    assert _split_double_time_bars(bars, fill_hits) == bars


def test_partial_final_bar_keeps_its_full_measure_tempo(tmp_path: Path, monkeypatch):
    from taikolab.ingest import tab_sync

    audio_path = tmp_path / "audio.wav"
    audio_path.touch()
    midi_path = tmp_path / "input.mid"
    midi_path.touch()
    bars = [
        (0.0, 2.5, 96.0, 4, 4),
        (2.5, 5.0, 96.0, 4, 4),
        (5.0, 7.5, 96.0, 4, 4),
        (7.5, 10.0, 96.0, 4, 4),
        (10.0, 12.5, 96.0, 4, 4),
    ]
    monkeypatch.setattr(
        tab_sync,
        "discover_song_files",
        lambda _: (audio_path, StemPaths()),
    )
    monkeypatch.setattr(tab_sync, "get_audio_duration", lambda _: 11.25)
    monkeypatch.setattr(
        tab_sync,
        "_parse_midi_unified",
        lambda _: (bars, [], [], [], []),
    )

    analysis = tab_sync.import_midi_aligned(
        song_id="partial-final-bar",
        song_dir=tmp_path,
        midi_path=midi_path,
        manual_offset_sec=0.0,
    )

    assert analysis.tempo_regions[-1].bpm == 96.0
    assert analysis.tempo_regions[-1].end_sec == 11.25
    final_bar_beats = [beat.time_sec for beat in analysis.beats if beat.time_sec >= 10.0]
    assert final_bar_beats == [10.0, 10.625]
