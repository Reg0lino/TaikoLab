from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from taikolab.arranger.engine import arrange_song
from taikolab.config import load_config
from taikolab.schemas import (
    BeatEvent,
    DownbeatMarker,
    DrumHit,
    SongAnalysis,
    SongOverrides,
    TempoRegion,
)
from taikolab.ui.server import create_app, fill_missed_double_gaps, snap_taps_to_transients, straighten_steady_taps


def test_custom_downbeats_preserve_meters_and_build_exact_bars():
    cfg = load_config("punk_gallop")
    # 2 bars of 6/8 (~1.75s) followed by 2 bars of 4/4 (~1.16s)
    taps = [
        DownbeatMarker(time_sec=0.50, numerator=6, denominator=8),
        DownbeatMarker(time_sec=2.25, numerator=6, denominator=8),
        DownbeatMarker(time_sec=4.00, numerator=4, denominator=4),
        DownbeatMarker(time_sec=5.16, numerator=4, denominator=4),
        DownbeatMarker(time_sec=6.32, numerator=4, denominator=4),
    ]
    analysis = SongAnalysis(
        song_id="tap_meter_test",
        title="Tap Meter",
        audio_path="audio.wav",
        duration_sec=7.0,
        beats=[BeatEvent(time_sec=0.0, is_downbeat=True)],
        drum_hits=[
            DrumHit(time_sec=0.50, drum_class="kick", velocity=0.9),
            DrumHit(time_sec=4.00, drum_class="kick", velocity=0.9),
            DrumHit(time_sec=4.58, drum_class="snare", velocity=0.9),
        ],
    )
    ov = SongOverrides(song_id="tap_meter_test", custom_downbeats=taps)
    chart = arrange_song(analysis, cfg, overrides=ov)
    oni = [c for c in chart.courses if c.difficulty == "oni"][0]

    assert len(oni.measures) == 4
    assert (oni.measures[0].numerator, oni.measures[0].denominator) == (6, 8)
    assert (oni.measures[2].numerator, oni.measures[2].denominator) == (4, 4)
    assert abs(oni.measures[0].bpm - 102.86) < 0.5
    assert abs(oni.measures[2].bpm - 206.9) < 0.5


def test_snap_taps_to_transients_and_straighten_bpm():
    analysis = SongAnalysis(
        song_id="snap_test",
        title="Snap",
        audio_path="audio.wav",
        duration_sec=12.0,
        drum_hits=[
            DrumHit(time_sec=1.00, drum_class="kick", velocity=0.95),
            DrumHit(time_sec=3.00, drum_class="kick", velocity=0.95),
            DrumHit(time_sec=5.00, drum_class="kick", velocity=0.95),
            DrumHit(time_sec=7.00, drum_class="kick", velocity=0.95),
            DrumHit(time_sec=9.00, drum_class="kick", velocity=0.95),
        ],
    )
    # Human taps ~25ms late + slight jitter
    noisy_taps = [
        DownbeatMarker(time_sec=1.028),
        DownbeatMarker(time_sec=3.021),
        DownbeatMarker(time_sec=4.985),
        DownbeatMarker(time_sec=7.032),
        DownbeatMarker(time_sec=9.015),
    ]
    snapped, cnt = snap_taps_to_transients(noisy_taps, analysis, window_ms=45.0, tap_latency_ms=-10.0)
    assert cnt == 5
    for m, expected in zip(snapped, [1.0, 3.0, 5.0, 7.0, 9.0]):
        assert abs(m.time_sec - expected) < 0.002

    # Straighten jittery bars inside a steady section while preserving a 2x tempo jump
    jitter = [
        DownbeatMarker(time_sec=1.00),
        DownbeatMarker(time_sec=3.04),  # ~2.04s
        DownbeatMarker(time_sec=4.97),  # ~1.93s
        DownbeatMarker(time_sec=7.02),  # ~2.05s
        DownbeatMarker(time_sec=9.00),  # ~1.98s -> steady 4-bar run @ 2.00s!
        DownbeatMarker(time_sec=10.00), # sudden 2x fast bar (1.00s)!
    ]
    straight = straighten_steady_taps(jitter, tolerance_pct=6.0)
    assert abs(straight[1].time_sec - 3.00) < 0.01
    assert abs(straight[2].time_sec - 5.00) < 0.01
    assert abs(straight[3].time_sec - 7.00) < 0.01
    assert abs(straight[5].time_sec - 10.00) < 0.01


def test_fill_missed_double_gaps():
    taps = [
        DownbeatMarker(time_sec=0.0),
        DownbeatMarker(time_sec=2.0),
        DownbeatMarker(time_sec=6.0),  # missed bar at 4.0!
        DownbeatMarker(time_sec=8.0),
    ]
    filled, added = fill_missed_double_gaps(taps)
    assert added == 1
    assert len(filled) == 5
    assert abs(filled[2].time_sec - 4.0) < 0.01


def test_tap_endpoints_via_fastapi(tmp_path: Path, monkeypatch):
    song_dir = tmp_path / "tap_api_song"
    song_dir.mkdir()
    sf.write(str(song_dir / "audio.wav"), np.zeros(22050 * 6, dtype=np.float32), 22050)
    analysis = SongAnalysis(
        song_id="tap_api_song",
        title="Tap API",
        audio_path="audio.wav",
        duration_sec=6.0,
        drum_hits=[DrumHit(time_sec=1.0, drum_class="kick", velocity=0.9)],
    )
    (song_dir / "analysis.json").write_text(analysis.model_dump_json(), encoding="utf-8")

    from taikolab import config as cfg_mod
    real_load = cfg_mod.load_config

    def fake_load(preset_name=None, root_dir=None):
        c = real_load(preset_name, root_dir)
        c.settings.workspace_dir = str(tmp_path)
        return c

    import taikolab.ui.server as srv_mod
    monkeypatch.setattr(srv_mod, "load_config", fake_load)

    client = TestClient(create_app())
    r = client.post(
        "/api/songs/tap_api_song/taps/snap",
        json={"taps": [{"time_sec": 1.02}], "window_ms": 45.0, "tap_latency_ms": 0.0},
    )
    assert r.status_code == 200
    assert r.json()["snapped_count"] == 1