"""Sprint 2 tests: Section Customizer Matrix UI contracts and FastAPI blend endpoints."""

from pathlib import Path
from fastapi.testclient import TestClient

from taikolab.config import load_config
from taikolab.schemas import (
    BeatEvent,
    DrumHit,
    EnergyPoint,
    OnsetEvent,
    SectionDifficultyRule,
    SectionOverride,
    SongAnalysis,
    SongOverrides,
    TempoRegion,
)
from taikolab.ui.server import create_app


def test_static_ui_exposes_phrasing_blend_controls():
    static_dir = Path("src/taikolab/ui/static")
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    js = (static_dir / "app.js").read_text(encoding="utf-8")
    css = (static_dir / "styles.css").read_text(encoding="utf-8")

    # Matrix headers & controls
    assert "<th>Phrasing / Blend</th>" in html
    assert "ctrlDefaultBlendStyle" in html
    assert "ctrlDefaultPhrasingLead" in html
    assert "ctrlSelectivePhraseParts" in html
    assert "secBlendStyle" in html
    assert "secPhrasingLead" in html
    assert "laneHighwaySpeed" in html

    # JS handlers
    assert ".mx-blend" in js
    assert ".mx-lead" in js
    assert "blend_style: blendStyle" in js
    assert "phrasing_lead: phrasingLead" in js
    assert "laneHighwaySpeed" in js

    # CSS layout
    assert ".matrix-blend-cell" in css


def test_api_arrange_and_save_as_with_blend_style(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    song_dir = ws / "blend-test-song"
    song_dir.mkdir(parents=True)
    (song_dir / "audio.wav").write_bytes(b"RIFF0000WAVE")

    beats = [BeatEvent(time_sec=i * 0.5, is_downbeat=(i % 4 == 0)) for i in range(16)]
    drums = [DrumHit(time_sec=i * 0.5, drum_class="kick" if i % 2 == 0 else "snare", velocity=0.85) for i in range(16)]
    vocals = [OnsetEvent(time_sec=i * 0.5 + 0.25, strength=0.80) for i in range(8)]
    energy = [EnergyPoint(time_sec=i * 2.0 + 1.0, drums_rms=0.6, vocals_rms=0.5, guitar_rms=0.4) for i in range(4)]

    analysis = SongAnalysis(
        song_id="blend-test-song",
        title="Blend Test",
        audio_path="audio.wav",
        duration_sec=8.0,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=8.0, bpm=120.0)],
        drum_hits=drums,
        vocal_events=vocals,
        energy_curve=energy,
    )
    (song_dir / "analysis.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    cfg = load_config("punk_gallop")
    cfg.settings.workspace_dir = str(ws)
    cfg.settings.opentaiko_songs_dir = ""

    monkeypatch.setattr("taikolab.ui.server.load_config", lambda preset=None: cfg.model_copy(deep=True))

    app = create_app()
    client = TestClient(app)

    overrides = SongOverrides(
        song_id="blend-test-song",
        title="Blend Test",
        sections=[
            SectionOverride(
                start_sec=0.0,
                end_sec=8.0,
                label="Verse",
                follow_mode="drums_vocals",
                blend_style="alt_2bar",
                phrasing_lead="vocals",
                difficulty_rules={
                    "hard": SectionDifficultyRule(
                        follow_mode="drums_vocals",
                        blend_style="alt_2bar",
                        phrasing_lead="vocals",
                    )
                },
            )
        ],
    )

    resp = client.post("/api/songs/blend-test-song/arrange", json=overrides.model_dump())
    assert resp.status_code == 200
    data = resp.json()
    assert data["overrides"]["sections"][0]["blend_style"] == "alt_2bar"
    assert data["overrides"]["sections"][0]["phrasing_lead"] == "vocals"

    # Test Save-As (-s) preserves blend_style & phrasing_lead
    save_as_resp = client.post(
        "/api/songs/blend-test-song/save-as",
        json={
            "new_song_id": "blend-test-song-s",
            "new_title": "Blend Test-s",
            "overrides": overrides.model_dump(),
            "export_to_sim": False,
        },
    )
    assert save_as_resp.status_code == 200
    cloned = save_as_resp.json()
    assert cloned["song_id"] == "blend-test-song-s"
    assert cloned["overrides"]["sections"][0]["blend_style"] == "alt_2bar"
    assert cloned["overrides"]["sections"][0]["difficulty_rules"]["hard"]["phrasing_lead"] == "vocals"
