from pathlib import Path

from fastapi.testclient import TestClient

from taikolab.schemas import SongOverrides, TaikoNote
from taikolab.ui.server import create_app
from taikolab.ui.tja_editor import apply_tja_edits, load_tja, render_tja_edits, save_tja_edits

_TJA_TEXT = """TITLE:Imported Test
SUBTITLE:--TaikoLab
BPM:120
WAVE:missing.wav
OFFSET:0
DEMOSTART:0
GENRE:Punk

COURSE:Hard
LEVEL:5
BALLOON:

#START
#SCROLL 0.500
#MEASURE 4/4
#BPMCHANGE 120
1000000000000000,
0000000000000000,
#END
"""


def _write_tja(path: Path) -> bytes:
    original = _TJA_TEXT.encode("utf-8")
    path.write_bytes(original)
    return original


def test_tja_import_edit_save_preserves_source_and_backs_it_up(tmp_path: Path):
    path = tmp_path / "imported.tja"
    original = _write_tja(path)
    document = load_tja(path)
    assert render_tja_edits(document, document.chart) == original
    overrides = SongOverrides(
        song_id="imported",
        note_edits={
            "hard:0:p1/64": TaikoNote.KAT.value,
            "hard:1:p1/16": TaikoNote.BALLOON.value,
            "hard:1:p3/16": TaikoNote.END.value,
        },
        balloon_counts_by_course={"hard": [7]},
    )

    chart = apply_tja_edits(document, overrides)
    saved = render_tja_edits(document, chart).decode("utf-8")
    assert "BALLOON:7" in saved
    assert "1200000000000000000000000000000000000000000000000000000000000000," in saved
    assert f"0708{'0' * 12}," in saved

    save_tja_edits(document, chart)
    assert path.with_suffix(".tja.bak").read_bytes() == original
    reopened = load_tja(path)
    hard = reopened.chart.courses[0]
    assert hard.measures[0].notes[1] == TaikoNote.KAT.value
    assert hard.measures[1].notes[1] == TaikoNote.BALLOON.value
    assert hard.balloon_counts == [7]


def test_imported_tja_api_edits_and_saves_without_analysis_json(tmp_path: Path):
    path = tmp_path / "existing-chart.tja"
    _write_tja(path)
    client = TestClient(create_app())

    imported = client.post("/api/import-tja", json={"path": str(path)})
    assert imported.status_code == 200
    import_id = imported.json()["import_id"]
    payload = client.get(f"/api/imported/{import_id}")
    assert payload.status_code == 200
    assert payload.json()["analysis_summary"]["title"] == "Imported Test"
    assert payload.json()["analysis_summary"]["stems_available"] == []

    overrides = SongOverrides(
        song_id=import_id,
        note_edits={"hard:0:p1/16": TaikoNote.BIG_DON.value},
    ).model_dump()
    arranged = client.post(f"/api/imported/{import_id}/arrange", json=overrides)
    assert arranged.status_code == 200
    assert arranged.json()["chart"]["courses"][0]["measures"][0]["notes"][1] == TaikoNote.BIG_DON.value

    saved = client.post(f"/api/imported/{import_id}/export", json=overrides)
    assert saved.status_code == 200
    assert saved.json()["exported_path"] == str(path.resolve())
    assert saved.json()["overrides"]["note_edits"] == {}
    assert "1300000000000000," in path.read_text(encoding="utf-8")
    assert path.with_suffix(".tja.bak").exists()
    reopened = client.get(f"/api/imported/{import_id}")
    assert reopened.json()["chart"]["courses"][0]["measures"][0]["notes"][1] == TaikoNote.BIG_DON.value


def test_tja_import_rejects_branching_charts(tmp_path: Path):
    path = tmp_path / "branching.tja"
    path.write_text(_TJA_TEXT.replace("#START", "#START\n#BRANCHSTART p,50,80"), encoding="utf-8")

    response = TestClient(create_app()).post("/api/import-tja", json={"path": str(path)})

    assert response.status_code == 400
    assert "Branching TJA charts" in response.json()["detail"]
def test_tja_editor_updates_bpmchange_and_offset_when_barlines_warped(tmp_path: Path):
    path = tmp_path / "timing-edit.tja"
    _write_tja(path)
    document = load_tja(path)

    overrides = SongOverrides(
        song_id="imported",
        barline_times=[0.0, 2.4, 4.0],
        global_offset_ms=50.0,
    )
    chart = apply_tja_edits(document, overrides)
    save_tja_edits(document, chart)

    text = path.read_text(encoding="utf-8")
    assert "OFFSET:-0.050" in text
    assert "#BPMCHANGE 100.000" in text
    assert "#BPMCHANGE 150.000" in text

    reloaded = load_tja(path)
    hard = reloaded.chart.courses[0]
    assert hard.measures[0].start_sec == 0.05
    assert round(hard.measures[0].bpm, 1) == 100.0
    assert round(hard.measures[1].bpm, 1) == 150.0
    assert hard.measures[0].notes[0] == TaikoNote.DON.value