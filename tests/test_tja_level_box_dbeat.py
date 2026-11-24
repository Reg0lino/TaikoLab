"""Tests for 00 _TaikoLab AutoCharts + box.def export, robust TJA LEVEL/audio, and halftime D-beat triples."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from taikolab.arranger.engine import arrange_song
from taikolab.config import load_config
from taikolab.exporters.tja_writer import AUTOCONTAINER_FOLDER, export_to_opentaiko
from taikolab.schemas import BeatEvent, DrumHit, SongAnalysis, SongOverrides, TempoRegion
from taikolab.ui.server import create_app
from taikolab.ui.tja_editor import load_tja


def test_export_places_chart_inside_00_autocharts_and_writes_box_def(tmp_path: Path) -> None:
    songs_dir = tmp_path / "Songs"
    songs_dir.mkdir()
    src_tja = tmp_path / "test-auto.tja"
    src_wav = tmp_path / "audio.wav"
    src_tja.write_text("TITLE:Test\nBPM:150\n#START\n1000,\n#END\n", encoding="utf-8")
    src_wav.write_bytes(b"RIFF")

    exported = export_to_opentaiko(src_tja, src_wav, str(songs_dir), folder_name="test-auto")
    assert exported == songs_dir / AUTOCONTAINER_FOLDER / "test-auto" / "test-auto.tja"
    assert exported.is_file()
    assert (songs_dir / AUTOCONTAINER_FOLDER / "test-auto" / "audio.wav").is_file()
    box_def = (songs_dir / AUTOCONTAINER_FOLDER / "box.def").read_text(encoding="utf-8")
    assert "#TITLE:TaikoLab AutoCharts" in box_def
    assert "#SCENEPRESET:Clouds" in box_def
    assert not (songs_dir / "test-auto").exists()


def test_tja_loader_handles_decimal_comment_and_empty_edit_level_plus_audio_upload(tmp_path: Path) -> None:
    tja = tmp_path / "pack_chart.tja"
    tja.write_text(
        "TITLE:Pack Chart\nBPM:160\nWAVE:song.ogg\nOFFSET:-0.5\n"
        "COURSE:Oni\nLEVEL:9.5 // spicy\n#START\n10201020,\n#END\n"
        "COURSE:Edit\nLEVEL:\n#START\n1000,\n#END\n",
        encoding="utf-8",
    )
    doc = load_tja(tja)
    assert [c.level_stars for c in doc.chart.courses] == [10, 5]
    assert doc.audio_path is None

    client = TestClient(create_app())
    res_imp = client.post("/api/import-tja", json={"path": str(tja)})
    assert res_imp.status_code == 200
    assert res_imp.json()["has_audio"] is False
    imp_id = res_imp.json()["import_id"]

    res_up = client.post(
        f"/api/imported/{imp_id}/upload-audio?filename=attached.mp3",
        content=b"ID3dummy",
        headers={"Content-Type": "audio/mpeg"},
    )
    assert res_up.status_code == 200
    assert res_up.json()["has_audio"] is True
    assert (tmp_path / "attached.mp3").is_file()


def test_phoenix_halftime_dbeat_gets_uniform_32_grid_and_alternating_triples() -> None:
    cfg = load_config("taiko_auto")
    bar_dur = 240.0 / 112.0
    beats = []
    drums = []
    for b in range(8):
        s = b * bar_dur
        for q in range(4):
            beats.append(BeatEvent(time_sec=s + q * (bar_dur / 4.0), is_downbeat=(q == 0)))
        if b % 2 == 0:
            drums.append(DrumHit(time_sec=s, drum_class="crash", velocity=0.95, source="tab"))
        slots = [0, 4, 6, 10, 12, 16, 20, 22, 26, 28]
        if b in (1, 5):
            slots.append(18)
        for sl in slots:
            drums.append(
                DrumHit(
                    time_sec=s + sl * (bar_dur / 32.0),
                    drum_class="snare" if sl in (4, 12, 20, 28) else "kick",
                    velocity=0.90,
                    source="tab",
                )
            )

    analysis = SongAnalysis(
        song_id="phoenix-dbeat",
        title="Phoenix D-Beat",
        audio_path="audio.wav",
        duration_sec=8 * bar_dur,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=8 * bar_dur, bpm=112.0)],
        drum_hits=drums,
    )
    ov = SongOverrides(song_id="phoenix-dbeat", groove_triple_density_by_course={"hard": 1.0}, groove_triple_max_hits=6)
    chart = arrange_song(analysis, cfg, overrides=ov)
    hard = next(c for c in chart.courses if c.difficulty == "hard")
    assert all(len(m.notes) == 32 for m in hard.measures)
    first_half_triples = sum(1 for m in hard.measures if m.notes[1] != 0 or m.notes[2] != 0 or m.notes[8] != 0 or m.notes[9] != 0)
    second_half_triples = sum(1 for m in hard.measures if m.notes[17] != 0 or m.notes[18] != 0 or m.notes[24] != 0 or m.notes[25] != 0)
    assert first_half_triples >= 1 and second_half_triples >= 1