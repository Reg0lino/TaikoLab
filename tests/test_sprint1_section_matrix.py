"""Sprint 1 tests: per-section scroll, density scaling, isolated triples + max_hits, color flip, and -s save-as."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from taikolab.arranger.engine import arrange_song
from taikolab.config import apply_chart_controls_to_config, load_config
from taikolab.exporters.tja_writer import render_tja_text
from taikolab.schemas import (
    BeatEvent,
    DrumHit,
    SectionDifficultyRule,
    SectionOverride,
    SongAnalysis,
    SongOverrides,
    TaikoNote,
    TempoRegion,
)
from taikolab.ui.server import create_app


def _make_two_section_song() -> SongAnalysis:
    beats = []
    drums = []
    for b in range(8):
        start = b * 1.6
        for q in range(4):
            t = start + q * 0.4
            beats.append(BeatEvent(time_sec=t, is_downbeat=(q == 0)))
            drums.append(
                DrumHit(
                    time_sec=t,
                    drum_class="kick" if q in (0, 2) else "snare",
                    velocity=0.85,
                    source="tab",
                )
            )
    return SongAnalysis(
        song_id="sec-matrix-test",
        title="Matrix Test",
        audio_path="audio.wav",
        duration_sec=12.8,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=12.8, bpm=150.0)],
        drum_hits=drums,
    )


def test_per_section_scroll_density_triples_and_color_invert_stay_isolated() -> None:
    cfg = load_config("taiko_auto")
    analysis = _make_two_section_song()
    ov = SongOverrides(
        song_id="sec-matrix-test",
        note_edits={"hard:0:p4/16": TaikoNote.KAT.value},
        sections=[
            SectionOverride(
                start_sec=0.0,
                end_sec=6.4,
                label="Verse",
                difficulty_rules={
                    "hard": SectionDifficultyRule(
                        follow_mode="drums",
                        scroll_multiplier=0.80,
                        density_scale=0.50,
                        groove_triple_density=0.0,
                        invert_colors=True,
                    )
                },
            ),
            SectionOverride(
                start_sec=6.4,
                end_sec=12.8,
                label="Chorus",
                difficulty_rules={
                    "hard": SectionDifficultyRule(
                        follow_mode="drums",
                        scroll_multiplier=1.40,
                        density_scale=1.0,
                        groove_triple_density=1.0,
                        invert_colors=False,
                    )
                },
            ),
        ],
    )
    apply_chart_controls_to_config(cfg, ov)
    chart = arrange_song(analysis, cfg, overrides=ov)
    hard = next(c for c in chart.courses if c.difficulty == "hard")

    assert hard.measures[0].scroll == 0.80
    assert hard.measures[4].scroll == 1.40
    tja = render_tja_text(chart, normalize_scroll=True, target_highway_speed=150.0)
    assert "#SCROLL 0.800" in tja
    assert "#SCROLL 1.400" in tja

    assert hard.measures[0].notes[4] == TaikoNote.KAT.value
    for m in hard.measures[:4]:
        hit_count = sum(1 for n in m.notes if n in (1, 2, 3, 4))
        assert hit_count == 2
        assert m.notes[1] == 0 and m.notes[2] == 0

    chorus_triples = sum(
        1 for m in hard.measures[4:]
        if m.notes[1] != 0 or m.notes[2] != 0 or m.notes[9] != 0 or m.notes[10] != 0
    )
    assert chorus_triples >= 1


def test_groove_triple_max_hits_allows_7hit_posthardcore_beat_when_raised() -> None:
    cfg = load_config("taiko_auto")
    beats = []
    drums = []
    for b in range(4):
        s = b * 1.6
        for q in range(4):
            beats.append(BeatEvent(time_sec=s + q * 0.4, is_downbeat=(q == 0)))
        for slot16, cls in ((0, "kick"), (2, "kick"), (4, "snare"), (6, "kick"), (8, "kick"), (10, "kick"), (12, "snare")):
            drums.append(DrumHit(time_sec=s + slot16 * 0.1, drum_class=cls, velocity=0.9, source="tab"))

    analysis = SongAnalysis(
        song_id="dense7",
        title="Dense 7",
        audio_path="audio.wav",
        duration_sec=6.4,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=6.4, bpm=150.0)],
        drum_hits=drums,
    )
    ov_default = SongOverrides(song_id="dense7", groove_triple_density_by_course={"hard": 1.0}, groove_triple_max_hits=6)
    ov_raised = SongOverrides(song_id="dense7", groove_triple_density_by_course={"hard": 1.0}, groove_triple_max_hits=8)

    c_def = arrange_song(analysis, cfg, overrides=ov_default)
    c_hi = arrange_song(analysis, cfg, overrides=ov_raised)
    h_def = next(c for c in c_def.courses if c.difficulty == "hard")
    h_hi = next(c for c in c_hi.courses if c.difficulty == "hard")
    assert sum(sum(1 for n in m.notes if n != 0) for m in h_hi.measures) > sum(
        sum(1 for n in m.notes if n != 0) for m in h_def.measures
    )


def test_save_as_sectioned_song_endpoint_clones_and_exports_cleanly(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "workspace"
    sim = tmp_path / "Songs"
    song_dir = ws / "demo-auto"
    song_dir.mkdir(parents=True)
    sim.mkdir(parents=True)

    analysis = _make_two_section_song().model_copy(update={"song_id": "demo-auto", "title": "Demo --autov0.2"})
    (song_dir / "analysis.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    (song_dir / "audio.wav").write_bytes(b"RIFFdemo")

    base_cfg = load_config("taiko_auto")
    base_cfg.settings.workspace_dir = str(ws)
    base_cfg.settings.opentaiko_songs_dir = str(sim)
    monkeypatch.setattr("taikolab.ui.server.load_config", lambda *_a, **_kw: base_cfg.model_copy(deep=True))

    client = TestClient(create_app())
    ov = SongOverrides(song_id="demo-auto", title="Demo --autov0.2")
    res = client.post(
        "/api/songs/demo-auto/save-as",
        json={"new_song_id": "demo-auto-s", "new_title": "Demo --autov0.2-s", "overrides": ov.model_dump(), "export_to_sim": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["song_id"] == "demo-auto-s"
    assert (ws / "demo-auto-s" / "demo-auto-s.tja").is_file()
    assert (sim / "00 _TaikoLab AutoCharts" / "demo-auto-s" / "demo-auto-s.tja").is_file()
    assert (sim / "00 _TaikoLab AutoCharts" / "demo-auto-s" / "audio.wav").is_file()