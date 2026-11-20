from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from taikolab.schemas import (
    BeatEvent,
    DrumHit,
    SectionOverride,
    SongAnalysis,
    SongOverrides,
    TaikoNote,
    TempoRegion,
)
from taikolab.ui.server import create_app


def test_section_shift_and_manual_note_edits_api(tmp_path: Path, monkeypatch):
    # Create a tiny synthetic workspace song inside tmp_path and point config workspace_dir to it
    song_dir = tmp_path / "ui_test_song"
    song_dir.mkdir()
    sr = 22050
    sf.write(str(song_dir / "audio.wav"), np.zeros(sr * 4, dtype=np.float32), sr)

    analysis = SongAnalysis(
        song_id="ui_test_song",
        title="UI Test",
        artist="Tester",
        audio_path="audio.wav",
        duration_sec=4.0,
        beats=[BeatEvent(time_sec=i * 0.5, is_downbeat=(i % 4 == 0)) for i in range(8)],
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=4.0, bpm=120.0, numerator=4, denominator=4)],
        drum_hits=[DrumHit(time_sec=0.5, drum_class="kick", velocity=0.9, source="tab")],
    )
    (song_dir / "analysis.json").write_text(analysis.model_dump_json(), encoding="utf-8")

    # Monkeypatch load_config inside ui.server so workspace points to tmp_path
    from taikolab import config as cfg_mod
    real_load = cfg_mod.load_config

    def fake_load(preset_name=None, root_dir=None):
        c = real_load(preset_name, root_dir)
        c.settings.workspace_dir = str(tmp_path)
        return c

    import taikolab.ui.server as srv_mod
    monkeypatch.setattr(srv_mod, "load_config", fake_load)

    client = TestClient(create_app())
    r_list = client.get("/api/songs")
    assert r_list.status_code == 200
    assert any(s["song_id"] == "ui_test_song" for s in r_list.json()["songs"])

    r_get = client.get("/api/songs/ui_test_song")
    assert r_get.status_code == 200
    payload = r_get.json()
    assert len(payload["chart"]["courses"]) >= 3
    assert payload["reference_hits"] == [
        {"time_sec": 0.5, "drum_class": "kick", "velocity": 0.9}
    ]

    # Test section shift + manual note edit survival through POST /arrange
    ov = SongOverrides(
        song_id="ui_test_song",
        preset="punk_gallop",
        sections=[SectionOverride(start_sec=0.0, end_sec=4.0, label="Intro", offset_shift_ms=50.0)],
        note_edits={"hard:0:2": TaikoNote.BIG_KAT.value},
    )
    r_arr = client.post("/api/songs/ui_test_song/arrange", json=ov.model_dump())
    assert r_arr.status_code == 200
    hard = [c for c in r_arr.json()["chart"]["courses"] if c["difficulty"] == "hard"][0]
    assert hard["measures"][0]["notes"][2] == TaikoNote.BIG_KAT.value

    r_fine = client.post(
        "/api/songs/ui_test_song/arrange",
        json=SongOverrides(
            song_id="ui_test_song",
            preset="punk_gallop",
            note_edits={"hard:0:p1/64": TaikoNote.KAT.value},
        ).model_dump(),
    )
    assert r_fine.status_code == 200
    fine_hard = next(c for c in r_fine.json()["chart"]["courses"] if c["difficulty"] == "hard")
    assert len(fine_hard["measures"][0]["notes"]) % 64 == 0
    assert fine_hard["measures"][0]["notes"][len(fine_hard["measures"][0]["notes"]) // 64] == TaikoNote.KAT.value

    balloon_overrides = SongOverrides(
        song_id="ui_test_song",
        preset="punk_gallop",
        note_edits={
            "hard:0:p1/16": TaikoNote.BALLOON.value,
            "hard:0:p3/16": TaikoNote.END.value,
        },
        balloon_counts_by_course={"hard": [10]},
    )
    r_balloon = client.post(
        "/api/songs/ui_test_song/arrange",
        json=balloon_overrides.model_dump(),
    )
    assert r_balloon.status_code == 200
    assert "BALLOON:10" in (song_dir / "ui_test_song.tja").read_text(encoding="utf-8")

    invalid_balloon = SongOverrides(
        song_id="ui_test_song",
        preset="punk_gallop",
        note_edits={"hard:0:p1/16": TaikoNote.BALLOON.value},
        balloon_counts_by_course={"hard": [10]},
    )
    r_invalid = client.post(
        "/api/songs/ui_test_song/arrange",
        json=invalid_balloon.model_dump(),
    )
    assert r_invalid.status_code == 422
def test_wizard_overrides_populate_default_sections_and_apply_chart_controls(tmp_path: Path, monkeypatch):
    song_dir = tmp_path / "wizard_song"
    song_dir.mkdir()
    sr = 22050
    sf.write(str(song_dir / "audio.wav"), np.zeros(sr * 4, dtype=np.float32), sr)

    analysis = SongAnalysis(
        song_id="wizard_song",
        title="Wizard Song",
        artist="Tester",
        audio_path="audio.wav",
        duration_sec=4.0,
        beats=[BeatEvent(time_sec=i * 0.5, is_downbeat=(i % 4 == 0)) for i in range(8)],
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=4.0, bpm=120.0, numerator=4, denominator=4)],
        drum_hits=[DrumHit(time_sec=0.5, drum_class="kick", velocity=0.82, source="tab")],
    )
    (song_dir / "analysis.json").write_text(analysis.model_dump_json(), encoding="utf-8")

    # Wizard writes overrides.json without sections and with custom slider settings
    (song_dir / "overrides.json").write_text(
        SongOverrides(
            song_id="wizard_song",
            preset="punk_gallop",
            target_highway_speed=130.0,
            big_note_threshold=0.70,
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    from taikolab import config as cfg_mod
    real_load = cfg_mod.load_config

    def fake_load(preset_name=None, root_dir=None):
        c = real_load(preset_name, root_dir)
        c.settings.workspace_dir = str(tmp_path)
        return c

    import taikolab.ui.server as srv_mod
    monkeypatch.setattr(srv_mod, "load_config", fake_load)

    client = TestClient(create_app())
    r_get = client.get("/api/songs/wizard_song")
    assert r_get.status_code == 200
    payload = r_get.json()
    assert len(payload["overrides"]["sections"]) >= 1
    hard = next(c for c in payload["chart"]["courses"] if c["difficulty"] == "hard")
    assert TaikoNote.BIG_DON.value in hard["measures"][0]["notes"]
    tja_text = (song_dir / "wizard_song.tja").read_text(encoding="utf-8")
    assert "#SCROLL 1.083" in tja_text
def test_chart_control_sliders_persist_and_apply_through_ui_arrange(tmp_path: Path, monkeypatch):
    song_dir = tmp_path / "slider_song"
    song_dir.mkdir()
    sr = 22050
    sf.write(str(song_dir / "audio.wav"), np.zeros(sr * 4, dtype=np.float32), sr)

    drums = []
    for bar in range(8):
        start = bar * 1.0
        for b in range(4):
            drums.append(DrumHit(time_sec=start + b * 0.25, drum_class="kick" if b % 2 == 0 else "snare", velocity=0.85))
    analysis = SongAnalysis(
        song_id="slider_song",
        title="Slider Song",
        artist="Tester",
        audio_path="audio.wav",
        duration_sec=8.0,
        beats=[BeatEvent(time_sec=i * 0.25, is_downbeat=(i % 4 == 0)) for i in range(32)],
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=8.0, bpm=240.0, numerator=4, denominator=4)],
        drum_hits=drums,
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

    from taikolab.schemas import DifficultyRuleOverride
    client = TestClient(create_app())
    ov = SongOverrides(
        song_id="slider_song",
        preset="punk_gallop",
        target_highway_speed=140.0,
        difficulty_overrides={
            "oni": DifficultyRuleOverride(follow_mode="drums", density_scale=0.5, phrase_variation=1.0),
        },
    )
    r_arr = client.post("/api/songs/slider_song/arrange", json=ov.model_dump())
    assert r_arr.status_code == 200
    saved = SongOverrides.model_validate_json((song_dir / "overrides.json").read_text(encoding="utf-8"))
    assert saved.target_highway_speed == 140.0
    assert saved.difficulty_overrides["oni"].density_scale == 0.5
    assert saved.difficulty_overrides["oni"].phrase_variation == 1.0