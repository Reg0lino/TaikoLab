from pathlib import Path

import mido

from taikolab.arranger.engine import arrange_song
from taikolab.config import load_config
from taikolab.exporters.diagnostics import analyze_course
from taikolab.ingest.tab_sync import _parse_midi_unified, _split_double_time_bars
from taikolab.schemas import BeatEvent, DrumHit, SongAnalysis, TempoRegion


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
