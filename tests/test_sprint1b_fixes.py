"""Tests for halftime 32-slot triples, sub-bar section splits, Hey gross intro repair, and delete API."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from taikolab.arranger.engine import arrange_song
from taikolab.config import load_config
from taikolab.ingest.tab_sync import _repair_broken_dtw_regions_with_drums
from taikolab.schemas import BeatEvent, DrumHit, OnsetEvent, SectionOverride, SongAnalysis, SongOverrides, TempoRegion
from taikolab.ui.server import create_app


def test_halftime_118bpm_voyeur_beat_gets_32_slots_and_triples() -> None:
    cfg = load_config("taiko_auto")
    beats = []
    drums = []
    bar_dur = 240.0 / 118.0
    for b in range(6):
        s = b * bar_dur
        for q in range(4):
            beats.append(BeatEvent(time_sec=s + q * (bar_dur / 4.0), is_downbeat=(q == 0)))
        for idx16 in (0, 2, 4, 6, 8, 10, 12, 16, 18, 20, 22, 24):
            t = s + idx16 * (bar_dur / 32.0)
            drums.append(DrumHit(time_sec=t, drum_class="kick" if idx16 in (0, 2, 6, 8, 10, 16, 18, 22, 24) else "snare", velocity=0.9, source="tab"))

    analysis = SongAnalysis(
        song_id="voyeur-ht",
        title="Voyeur HT",
        audio_path="audio.wav",
        duration_sec=6 * bar_dur,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=6 * bar_dur, bpm=118.0)],
        drum_hits=drums,
    )
    ov = SongOverrides(song_id="voyeur-ht", groove_triple_density_by_course={"hard": 1.0}, groove_triple_max_hits=6)
    chart = arrange_song(analysis, cfg, overrides=ov)
    hard = next(c for c in chart.courses if c.difficulty == "hard")
    assert len(hard.measures[0].notes) == 32
    total_hits = sum(sum(1 for n in m.notes if n in (1, 2, 3, 4)) for m in hard.measures)
    assert total_hits > 72


def test_sub_bar_section_split_switches_guitar_bass_solo_at_exact_timestamp() -> None:
    cfg = load_config("taiko_auto")
    beats = [BeatEvent(time_sec=i * 0.5, is_downbeat=(i % 4 == 0)) for i in range(8)]
    drums = [DrumHit(time_sec=t, drum_class="kick", velocity=0.9, source="tab") for t in (0.0, 0.5, 2.0, 2.5)]
    guitars = [OnsetEvent(time_sec=t, strength=0.45) for t in (1.0, 1.25, 1.5, 3.0)]
    analysis = SongAnalysis(
        song_id="subbar",
        title="Subbar",
        audio_path="audio.wav",
        duration_sec=4.0,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=4.0, bpm=120.0)],
        drum_hits=drums,
        guitar_events=guitars,
    )
    ov = SongOverrides(
        song_id="subbar",
        sections=[
            SectionOverride(start_sec=0.0, end_sec=1.0, label="Drums", follow_mode="drums"),
            SectionOverride(start_sec=1.0, end_sec=4.0, label="Bass Solo", follow_mode="guitar"),
        ],
    )
    chart = arrange_song(analysis, cfg, overrides=ov)
    hard = next(c for c in chart.courses if c.difficulty == "hard")
    assert sum(1 for n in hard.measures[0].notes if n in (1, 2, 3, 4)) >= 4


def test_pixies_hey_37s_drumless_intro_recovers_2_5s_lag() -> None:
    midi_drums = [
        DrumHit(time_sec=round(t, 4), drum_class="kick" if i % 2 == 0 else "snare", velocity=0.9, source="tab")
        for i, t in enumerate(np.arange(38.0, 90.0, 0.6))
    ]
    real_drums = [
        DrumHit(time_sec=round(h.time_sec + 2.48, 4), drum_class=h.drum_class, velocity=0.9, source="adtof")
        for h in midi_drums
    ]
    knots_m = np.arange(0.0, 92.0, 1.0, dtype=np.float64)
    knots_a = knots_m.copy()
    knots_m, knots_a, repaired = _repair_broken_dtw_regions_with_drums(
        knots_m, knots_a, midi_drums, real_drums, 0.0, 90.0
    )
    assert repaired >= 2
    assert abs(float(np.interp(45.0, knots_m, knots_a)) - 47.48) <= 0.04


def test_delete_song_endpoint_archives_workspace_and_simulator(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "workspace"
    sim = tmp_path / "Songs"
    (ws / "bad-song").mkdir(parents=True)
    (ws / "bad-song" / "analysis.json").write_text("{}", encoding="utf-8")
    (sim / "bad-song").mkdir(parents=True)
    (sim / "bad-song" / "bad-song.tja").write_text("TITLE:Bad", encoding="utf-8")

    base_cfg = load_config("taiko_auto")
    base_cfg.settings.workspace_dir = str(ws)
    base_cfg.settings.opentaiko_songs_dir = str(sim)
    monkeypatch.setattr("taikolab.ui.server.load_config", lambda *_a, **_kw: base_cfg.model_copy(deep=True))

    client = TestClient(create_app())
    res = client.delete("/api/songs/bad-song")
    assert res.status_code == 200
    assert not (ws / "bad-song").exists()
    assert not (sim / "bad-song").exists()
    assert list((ws / "_backups").glob("bad-song_deleted_*"))
    assert list((tmp_path / "_TaikoLabBackups" / "bad-song").glob("deleted_*"))