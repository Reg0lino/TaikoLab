from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from taikolab.schemas import BeatEvent, DrumHit, SectionOverride, SongAnalysis, SongOverrides, TaikoNote, TempoRegion
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
        drum_hits=[DrumHit(time_sec=0.5, drum_class="kick", velocity=0.9)],
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