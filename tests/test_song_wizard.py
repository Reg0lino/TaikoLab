import json
import queue
import shutil
import sys
import types
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from taikolab.config import load_config, load_wizard_preferences, save_wizard_preferences
from taikolab.metadata import (
    MetadataLookupError,
    read_audio_tags,
    save_audiometa_api_key,
    search_audiometa_tracks,
    suggest_title_artist_from_filename,
)
from taikolab.schemas import ChartControlSettings, DifficultyRuleOverride, SongOverrides
from taikolab.song_setup import (
    make_song_id,
    normalize_songs_directory,
    stage_song_inputs,
    write_build_manifest,
)
from taikolab.ui import wizard as wizard_module
from taikolab.ui.wizard import TaikoWizard


def test_opening_editor_again_raises_existing_view():
    class ExistingEditor:
        raised = False

        def lift(self):
            self.raised = True

    wizard = TaikoWizard.__new__(TaikoWizard)
    wizard.editor_view = ExistingEditor()

    wizard._show_editor("existing-song")

    assert wizard.editor_view.raised


def test_editor_can_open_without_building_a_new_song():
    wizard = TaikoWizard.__new__(TaikoWizard)
    wizard.last_built_song_id = None
    opened: list[str] = []
    wizard._show_editor = opened.append

    wizard._open_last_editor()

    assert opened == [""]


def test_open_existing_tja_navigates_the_embedded_editor(monkeypatch, tmp_path: Path):
    class FakeServer:
        url = "http://127.0.0.1:1234"

        def open_tja(self, path: Path) -> str:
            self.path = path
            return "import-id"

    class FakeWebView:
        def navigate(self, url: str) -> None:
            self.url = url

    class FakeLabel:
        def configure(self, **kwargs: str) -> None:
            self.text = kwargs["text"]

    path = tmp_path / "old-chart.tja"
    monkeypatch.setattr(
        wizard_module.filedialog,
        "askopenfilename",
        lambda **_kwargs: str(path),
    )
    wizard = TaikoWizard.__new__(TaikoWizard)
    wizard.editor_server = FakeServer()
    wizard.editor_webview = FakeWebView()
    wizard.editor_title_label = FakeLabel()

    wizard._open_existing_tja()

    assert wizard.editor_server.path == path
    assert wizard.editor_webview.url.endswith("/?import_id=import-id")
    assert wizard.editor_title_label.text.endswith("old-chart")


def test_song_id_is_safe_and_auto_suffixed():
    assert make_song_id("Blink-182", "The Rock Show") == "blink-182-the-rock-show-auto"
    assert make_song_id("Björk", "Army of Me", variant=2) == "bjork-army-of-me-2-auto"

    with pytest.raises(ValueError, match="letters or numbers"):
        make_song_id("!!!", "???")


def test_songs_directory_accepts_game_executable(tmp_path: Path):
    songs_dir = tmp_path / "Songs"
    songs_dir.mkdir()
    executable = tmp_path / "YataiDON.exe"
    executable.touch()

    assert normalize_songs_directory(executable) == songs_dir.resolve()
    assert normalize_songs_directory(songs_dir) == songs_dir.resolve()

    unrelated_folder = tmp_path / "unrelated"
    unrelated_folder.mkdir()
    with pytest.raises(FileNotFoundError, match="Could not find a Songs folder"):
        normalize_songs_directory(unrelated_folder)


def test_songs_directory_preference_overlays_config_without_rewriting_yaml(tmp_path: Path):
    project_root = Path(__file__).parents[1]
    shutil.copytree(project_root / "configs", tmp_path / "configs")
    (tmp_path / "pyproject.toml").touch()
    controls = ChartControlSettings(
        target_highway_speed=120.0,
        big_note_threshold=0.92,
        quiet_guitar_rms_threshold=0.45,
        fill_rolls_per_course=2,
        fill_rolls_by_course={"easy": 3, "normal": 3, "hard": 0, "oni": 0},
        difficulty_overrides={
            "hard": DifficultyRuleOverride(
                follow_mode="drums", density_scale=1.2, phrase_variation=0.5
            )
        },
    )
    save_wizard_preferences(
        tmp_path,
        "C:/Games/YataiDON/Songs",
        chart_controls=controls.model_dump(exclude_none=True),
    )
    audio_directory = tmp_path / "music"
    midi_directory = tmp_path / "tabs"
    audio_directory.mkdir()
    midi_directory.mkdir()
    save_wizard_preferences(tmp_path, last_audio_directory=str(audio_directory))
    save_wizard_preferences(tmp_path, last_midi_directory=str(midi_directory))

    cfg = load_config(root_dir=tmp_path)
    preferences = load_wizard_preferences(tmp_path)

    assert cfg.settings.opentaiko_songs_dir == "C:/Games/YataiDON/Songs"
    assert preferences["last_audio_directory"] == str(audio_directory)
    assert preferences["last_midi_directory"] == str(midi_directory)
    restored_controls = ChartControlSettings.model_validate(preferences["chart_controls"])
    assert restored_controls.target_highway_speed == 120.0
    assert restored_controls.big_note_threshold == 0.92
    assert restored_controls.fill_rolls_per_course == 2
    assert restored_controls.fill_rolls_by_course == {
        "easy": 3,
        "normal": 3,
        "hard": 0,
        "oni": 0,
    }
    assert restored_controls.difficulty_overrides["hard"].follow_mode == "drums"
    assert restored_controls.difficulty_overrides["hard"].phrase_variation == 0.5
    assert TaikoWizard._picker_initial_directory("", str(audio_directory)) == str(audio_directory)
    assert TaikoWizard._picker_initial_directory("", str(midi_directory)) == str(midi_directory)
    assert (
        TaikoWizard._saved_picker_directory(str(midi_directory), fallback=str(audio_directory))
        == str(midi_directory)
    )
    assert (
        TaikoWizard._saved_picker_directory(None, fallback=str(audio_directory))
        == str(audio_directory)
    )
    assert (tmp_path / "configs" / "default_settings.yaml").read_bytes() == (
        project_root / "configs" / "default_settings.yaml"
    ).read_bytes()


def test_wizard_collects_chart_sliders_into_validated_settings():
    class Value:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    wizard = TaikoWizard.__new__(TaikoWizard)
    wizard.highway_speed = Value(125.0)
    wizard.big_note_threshold = Value(0.9)
    wizard.quiet_guitar_threshold = Value(0.45)
    wizard.quiet_guitar_behavior = Value("Leave empty")
    wizard.fill_roll_caps = {
        "easy": Value(3),
        "normal": Value(2),
        "hard": Value(1),
        "oni": Value(0),
    }
    wizard.density_scales = {
        "easy": Value(80),
        "normal": Value(95),
        "hard": Value(110),
        "oni": Value(130),
    }
    wizard.phrase_variations = {"hard": Value(25), "oni": Value(75)}
    wizard.source_modes = {
        "easy": Value("Automatic"),
        "normal": Value("Drums"),
        "hard": Value("Balanced"),
        "oni": Value("Vocals"),
    }

    settings = wizard._chart_control_settings()

    assert settings.target_highway_speed == 125.0
    assert settings.big_note_threshold == 0.9
    assert settings.quiet_guitar_behavior == "empty"
    assert settings.fill_rolls_by_course == {
        "easy": 3,
        "normal": 2,
        "hard": 1,
        "oni": 0,
    }
    assert settings.difficulty_overrides["hard"].density_scale == 1.1
    assert settings.difficulty_overrides["oni"].phrase_variation == 0.75
    assert settings.difficulty_overrides["easy"].follow_mode == "auto"
    assert settings.difficulty_overrides["hard"].follow_mode == "hybrid"
    assert settings.difficulty_overrides["oni"].follow_mode == "vocals"


def test_slider_reset_button_restores_its_default(monkeypatch):
    class Widget:
        def __init__(self, *_args, **kwargs):
            self.command = kwargs.get("command")

        def grid(self, **_kwargs):
            pass

        def configure(self, **_kwargs):
            pass

    class Variable:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

        def trace_add(self, *_args):
            pass

    buttons = []

    class Button(Widget):
        def __init__(self, *_args, **kwargs):
            super().__init__(**kwargs)
            buttons.append(self)

    monkeypatch.setattr(wizard_module.ttk, "Label", Widget)
    monkeypatch.setattr(wizard_module.ttk, "Button", Button)
    monkeypatch.setattr(wizard_module.tk, "Scale", Widget)

    slider = Variable(124.0)
    TaikoWizard._add_slider(
        TaikoWizard.__new__(TaikoWizard),
        Widget(),
        0,
        "Highway speed",
        slider,
        70,
        160,
        1,
        "{:.0f}",
        default_value=101.343,
    )
    buttons[0].command()

    assert slider.get() == 101.343


def test_stage_song_inputs_copies_audio_and_midi_to_standard_names(tmp_path: Path):
    audio_source = tmp_path / "Blink-182 - Dumpweed.mp3"
    midi_source = tmp_path / "dumpweed.mid"
    audio_source.write_bytes(b"audio")
    midi_source.write_bytes(b"midi")
    song_dir = tmp_path / "workspace" / "blink-182-dumpweed-auto"

    audio_path, midi_path = stage_song_inputs(song_dir, audio_source, midi_source)

    assert audio_path == song_dir / "audio.mp3"
    assert midi_path == song_dir / "input.mid"
    assert audio_path.read_bytes() == b"audio"
    assert midi_path.read_bytes() == b"midi"
    assert audio_source.exists()
    assert midi_source.exists()


def test_stage_song_inputs_rejects_guitar_pro(tmp_path: Path):
    audio_source = tmp_path / "audio.ogg"
    gp_source = tmp_path / "song.gp"
    audio_source.touch()
    gp_source.touch()

    with pytest.raises(ValueError, match="MIDI files"):
        stage_song_inputs(tmp_path / "song-auto", audio_source, gp_source)


def test_audio_tags_and_filename_suggestion(monkeypatch, tmp_path: Path):
    fake_mutagen = types.ModuleType("mutagen")
    fake_mutagen.MutagenError = type("MutagenError", (Exception,), {})
    fake_mutagen.File = lambda _path, easy: types.SimpleNamespace(
        tags={"title": ["Tagged Title"], "artist": ["Tagged Artist"]}
    )
    monkeypatch.setitem(sys.modules, "mutagen", fake_mutagen)

    assert read_audio_tags(tmp_path / "track.mp3") == ("Tagged Title", "Tagged Artist")
    assert suggest_title_artist_from_filename(Path("Blink-182 - Dumpweed.mp3")) == (
        "Dumpweed",
        "Blink-182",
    )


def test_audiometa_search_sends_text_query_and_parses_results(monkeypatch):
    requests = []
    response_payload = {
        "tracks": {
            "items": [
                {
                    "id": "track-1",
                    "name": "Dumpweed",
                    "artists": [{"name": "Blink-182"}],
                    "duration_ms": 157000,
                    "href": "https://audiometa.io/track/track-1",
                }
            ]
        }
    }

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return nullcontext(
            types.SimpleNamespace(read=lambda: json.dumps(response_payload).encode("utf-8"))
        )

    monkeypatch.setattr("taikolab.metadata.urlopen", fake_urlopen)
    results = search_audiometa_tracks("Blink-182", "Dumpweed", "test-secret")

    request, timeout = requests[0]
    query = parse_qs(urlparse(request.full_url).query)
    assert query["q"] == ["Blink-182 Dumpweed"]
    assert request.get_header("Authorization") == "Bearer test-secret"
    assert timeout == 8.0
    assert results[0].display_text == "Blink-182 - Dumpweed (2:37)"


def test_audiometa_rejects_invalid_track_list(monkeypatch):
    response = types.SimpleNamespace(read=lambda: b'{"tracks": {"items": "invalid"}}')
    monkeypatch.setattr(
        "taikolab.metadata.urlopen",
        lambda *_args, **_kwargs: nullcontext(response),
    )

    with pytest.raises(MetadataLookupError, match="invalid track list"):
        search_audiometa_tracks("artist", "title", "test-secret")


def test_api_key_is_saved_through_the_os_keyring(monkeypatch):
    stored = []
    keyring_errors = types.ModuleType("keyring.errors")
    keyring_errors.KeyringError = type("KeyringError", (Exception,), {})
    keyring = types.ModuleType("keyring")
    keyring.__path__ = []
    keyring.get_keyring = lambda: types.SimpleNamespace(priority=1)
    keyring.set_password = lambda *values: stored.append(values)
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    monkeypatch.setitem(sys.modules, "keyring.errors", keyring_errors)

    save_audiometa_api_key("test-secret")

    assert len(stored) == 1
    assert stored[0][2] == "test-secret"


def test_build_manifest_contains_no_api_key(tmp_path: Path):
    manifest_path = write_build_manifest(
        tmp_path,
        song_id="artist-title-auto",
        artist="Artist",
        title="Title -auto",
        preset="alt_rock",
        audio_source=Path("source.mp3"),
        midi_source=Path("source.mid"),
        metadata_source="manual",
        status="building",
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["song_id"] == "artist-title-auto"
    assert manifest["audio_source_name"] == "source.mp3"
    assert "api_key" not in manifest


def test_failed_replacement_restores_previous_workspace_and_simulator_files(monkeypatch, tmp_path: Path):
    song_id = "artist-title-auto"
    song_dir = tmp_path / "workspace" / song_id
    song_dir.mkdir(parents=True)
    old_manifest = '{"status": "complete"}\n'
    (song_dir / "build_manifest.json").write_text(old_manifest, encoding="utf-8")
    (song_dir / "important.txt").write_text("keep this", encoding="utf-8")

    songs_dir = tmp_path / "Songs"
    simulator_song = songs_dir / song_id
    simulator_song.mkdir(parents=True)
    existing_files = {
        f"{song_id}.tja": "old chart",
        "audio.mp3": "old audio",
        "audio_clean.wav": "old clean audio",
    }
    for name, contents in existing_files.items():
        (simulator_song / name).write_text(contents, encoding="utf-8")

    audio_source = tmp_path / "new.mp3"
    midi_source = tmp_path / "new.mid"
    audio_source.write_bytes(b"new audio")
    midi_source.write_bytes(b"new midi")

    class FailedProcess:
        stdout = iter(["simulated child output\n"])

        def wait(self):
            return 7

    monkeypatch.setattr("taikolab.ui.wizard.subprocess.Popen", lambda *_args, **_kwargs: FailedProcess())
    wizard = TaikoWizard.__new__(TaikoWizard)
    wizard.cfg = types.SimpleNamespace(
        root_dir=tmp_path,
        song_dir=lambda _song_id: song_dir,
    )
    wizard.events = queue.Queue()

    wizard._build_worker(
        song_id,
        audio_source,
        midi_source,
        "Title -auto",
        "Artist",
        "alt_rock",
        "manual",
        songs_dir,
        True,
        ChartControlSettings(
            target_highway_speed=120.0,
            fill_rolls_per_course=1,
            difficulty_overrides={
                "oni": DifficultyRuleOverride(density_scale=1.2, phrase_variation=0.6)
            },
        ),
    )

    assert (song_dir / "important.txt").read_text(encoding="utf-8") == "keep this"
    assert (song_dir / "build_manifest.json").read_text(encoding="utf-8") == old_manifest
    for name, contents in existing_files.items():
        assert (simulator_song / name).read_text(encoding="utf-8") == contents
    simulator_backups = list((songs_dir.parent / "_TaikoLabBackups" / song_id).glob("*"))
    assert len(simulator_backups) == 1
    assert {
        path.name: path.read_text(encoding="utf-8")
        for path in simulator_backups[0].iterdir()
    } == existing_files
    assert not (songs_dir / "_TaikoLabBackups").exists()
    failed_song_dirs = list((tmp_path / "workspace" / "_failed_builds").iterdir())
    assert len(failed_song_dirs) == 1
    failed_manifest = json.loads(
        (failed_song_dirs[0] / "build_manifest.json").read_text(encoding="utf-8")
    )
    failed_overrides = SongOverrides.model_validate_json(
        (failed_song_dirs[0] / "overrides.json").read_text(encoding="utf-8")
    )
    assert failed_manifest["status"] == "failed"
    assert failed_overrides.target_highway_speed == 120.0
    assert failed_overrides.difficulty_overrides["oni"].phrase_variation == 0.6
    assert any(event == "build_error" for event, _value in list(wizard.events.queue))
