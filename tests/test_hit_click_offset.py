"""Tests for 1ms hit click offset preference persistence and UI controls."""

from pathlib import Path
from fastapi.testclient import TestClient

from taikolab.config import load_config
from taikolab.ui.server import create_app


def test_hit_click_offset_ui_and_persistence(tmp_path, monkeypatch):
    cfg = load_config("punk_gallop")
    fake_root = tmp_path / "proj"
    (fake_root / "configs").mkdir(parents=True)
    cfg.root_dir = fake_root

    monkeypatch.setattr("taikolab.ui.server.load_config", lambda preset=None: cfg.model_copy(deep=True))

    app = create_app()
    client = TestClient(app)

    # Default should be 0.0
    r0 = client.get("/api/ui-preferences")
    assert r0.status_code == 200
    assert r0.json()["hit_click_offset_ms"] == 0.0

    # Save -17 ms (pull earlier)
    r1 = client.post("/api/ui-preferences", json={"hit_click_offset_ms": -17})
    assert r1.status_code == 200
    assert r1.json()["hit_click_offset_ms"] == -17.0

    # Verify fresh read returns -17.0
    r2 = client.get("/api/ui-preferences")
    assert r2.status_code == 200
    assert r2.json()["hit_click_offset_ms"] == -17.0

    # Verify static UI placement between hitVol and speedSelect
    html = Path("src/taikolab/ui/static/index.html").read_text(encoding="utf-8")
    idx_hit = html.index('id="hitVol"')
    idx_off = html.index('id="hitClickOffsetMs"')
    idx_spd = html.index('id="speedSelect"')
    assert idx_hit < idx_off < idx_spd

    js = Path("src/taikolab/ui/static/app.js").read_text(encoding="utf-8")
    assert "hitClickOffsetMs" in js
    assert "/api/ui-preferences" in js
