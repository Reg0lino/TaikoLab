"""Configuration models and YAML preset loader for TaikoLab."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from taikolab.schemas import ChartControlSettings, DrumClass, FollowMode, SongOverrides, TaikoTarget


class GlobalSettings(BaseModel):
    workspace_dir: str = "workspace"
    opentaiko_songs_dir: str = ""
    global_audio_offset_ms: float = 0.0
    smooth_bpm_deadzone: float = Field(default=2.5, ge=0.0, le=20.0)
    normalize_scroll_on_bpm_change: bool = True
    target_highway_speed: float = Field(default=101.343, gt=0.0)
    quiet_guitar_rms_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    default_preset: str = "punk_gallop"


class HoriProfile(BaseModel):
    max_nps_easy: float = Field(default=2.2, gt=0.5)
    max_nps_normal: float = Field(default=3.8, gt=0.5)
    max_nps_hard: float = Field(default=6.8, gt=0.5)
    max_nps_oni: float = Field(default=11.5, gt=0.5)

    min_gap_ms_easy: float = Field(default=240.0, ge=20.0)
    min_gap_ms_normal: float = Field(default=135.0, ge=20.0)
    min_gap_ms_hard: float = Field(default=80.0, ge=20.0)
    min_gap_ms_oni: float = Field(default=55.0, ge=20.0)

    blast_bpm_threshold: float = Field(default=185.0, gt=60.0)
    min_roll_duration_sec: float = Field(default=0.35, gt=0.05)
    min_note_gap_ms: float = Field(default=55.0, ge=10.0)
    prefer_stick_alternation: bool = True

    def max_nps_for(self, difficulty: str) -> float:
        mapping = {
            "easy": self.max_nps_easy,
            "normal": self.max_nps_normal,
            "hard": self.max_nps_hard,
            "oni": self.max_nps_oni,
            "ura": self.max_nps_oni + 2.0,
        }
        return mapping.get(difficulty, self.max_nps_hard)

    def min_gap_ms_for(self, difficulty: str) -> float:
        mapping = {
            "easy": self.min_gap_ms_easy,
            "normal": self.min_gap_ms_normal,
            "hard": self.min_gap_ms_hard,
            "oni": self.min_gap_ms_oni,
            "ura": max(42.0, self.min_gap_ms_oni - 10.0),
        }
        return mapping.get(difficulty, self.min_gap_ms_hard)


class GenrePreset(BaseModel):
    name: str
    description: str = ""
    normal_follow_mode: FollowMode = "vocals"
    hard_follow_mode: FollowMode = "drums"
    oni_follow_mode: FollowMode = "hybrid"
    fold_blasts_to_rolls: bool = True
    enable_big_notes: bool = True
    big_note_threshold: float = Field(default=0.88, ge=0.1, le=1.0)
    hihat_min_velocity: float = Field(default=0.88, ge=0.0, le=1.0)
    vocal_min_strength: float = Field(default=0.36, ge=0.0, le=1.0)
    guitar_min_strength: float = Field(default=0.58, ge=0.0, le=1.0)
    drum_mapping: dict[DrumClass, TaikoTarget] = Field(
        default_factory=lambda: {
            "kick": "don",
            "tom": "don",
            "snare": "kat",
            "crash": "kat",
            "ride": "kat",
            "hihat": "ignore",
            "unknown": "don",
        }
    )


class AppConfig(BaseModel):
    root_dir: Path
    settings: GlobalSettings
    hori: HoriProfile
    preset: GenrePreset

    def song_dir(self, song_id: str) -> Path:
        ws = Path(self.settings.workspace_dir)
        if not ws.is_absolute():
            ws = self.root_dir / ws
        return ws / song_id


def find_project_root(start: Path | None = None) -> Path:
    cur = (start or Path(__file__)).resolve()
    if cur.is_file():
        cur = cur.parent
    for candidate in [cur, *cur.parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "configs").exists():
            return candidate
    raise FileNotFoundError("Could not locate TaikoLab project root containing configs/ and pyproject.toml.")


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def load_wizard_preferences(root_dir: Path) -> dict[str, object]:
    """Load local desktop preferences without exposing them as global YAML settings."""
    prefs_path = root_dir / "configs" / "wizard_settings.json"
    if prefs_path.exists():
        loaded = json.loads(prefs_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"Wizard preferences must be a JSON object: {prefs_path}")
        return loaded
    return {}


def save_wizard_preferences(
    root_dir: Path,
    opentaiko_songs_dir: str | None = None,
    *,
    last_audio_directory: str | None = None,
    last_midi_directory: str | None = None,
    last_gp_directory: str | None = None,
    chart_controls: dict[str, object] | None = None,
    use_demucs_stems: bool | None = None,
    append_version_tag: bool | None = None,
    ingest_mode: str | None = None,
    demucs_model: str | None = None,
    slider_profiles: dict[str, dict[str, object]] | None = None,
    hit_click_offset_ms: float | None = None,
) -> Path:
    """Persist desktop-only settings without rewriting the commented YAML config."""
    prefs_path = root_dir / "configs" / "wizard_settings.json"
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    preferences = load_wizard_preferences(root_dir)
    if opentaiko_songs_dir is not None:
        preferences["opentaiko_songs_dir"] = opentaiko_songs_dir
    if last_audio_directory is not None:
        preferences["last_audio_directory"] = last_audio_directory
    if last_midi_directory is not None:
        preferences["last_midi_directory"] = last_midi_directory
    if last_gp_directory is not None:
        preferences["last_gp_directory"] = last_gp_directory
    if chart_controls is not None:
        controls = ChartControlSettings.model_validate(chart_controls)
        preferences["chart_controls"] = controls.model_dump(exclude_none=True)
    if use_demucs_stems is not None:
        preferences["use_demucs_stems"] = bool(use_demucs_stems)
    if append_version_tag is not None:
        preferences["append_version_tag"] = bool(append_version_tag)
    if ingest_mode is not None:
        preferences["ingest_mode"] = ingest_mode
    if demucs_model is not None:
        preferences["demucs_model"] = demucs_model
    if slider_profiles is not None:
        preferences["slider_profiles"] = slider_profiles
    if hit_click_offset_ms is not None:
        preferences["hit_click_offset_ms"] = float(hit_click_offset_ms)

    temp_path = prefs_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(preferences, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(prefs_path)
    return prefs_path


def load_config(preset_name: str | None = None, root_dir: Path | None = None) -> AppConfig:
    root = find_project_root(root_dir)
    configs_dir = root / "configs"

    settings = GlobalSettings(**_load_yaml(configs_dir / "default_settings.yaml"))
    preferences = load_wizard_preferences(root)
    songs_dir = preferences.get("opentaiko_songs_dir")
    if songs_dir is not None:
        if not isinstance(songs_dir, str):
            raise ValueError(f"opentaiko_songs_dir must be text: {configs_dir / 'wizard_settings.json'}")
        settings.opentaiko_songs_dir = songs_dir
    hori = HoriProfile(**_load_yaml(configs_dir / "hori_default.yaml"))

    chosen_preset = preset_name or settings.default_preset
    preset_path = configs_dir / "presets" / f"{chosen_preset}.yaml"
    preset = GenrePreset(**_load_yaml(preset_path))

    return AppConfig(
        root_dir=root,
        settings=settings,
        hori=hori,
        preset=preset,
    )

def apply_chart_controls_to_config(cfg: AppConfig, overrides: SongOverrides | None) -> AppConfig:
    """Apply slider/toggle overrides onto AppConfig cleanly (without requiring YAML preset edits)."""
    if overrides is None:
        return cfg
    if overrides.target_highway_speed is not None:
        cfg.settings.target_highway_speed = overrides.target_highway_speed
    if overrides.quiet_guitar_rms_threshold is not None:
        cfg.settings.quiet_guitar_rms_threshold = overrides.quiet_guitar_rms_threshold
    if overrides.big_note_threshold is not None:
        cfg.preset.big_note_threshold = overrides.big_note_threshold
    if overrides.enable_big_notes is not None:
        cfg.preset.enable_big_notes = overrides.enable_big_notes
    if overrides.fold_blasts_to_rolls is not None:
        cfg.preset.fold_blasts_to_rolls = overrides.fold_blasts_to_rolls
    if overrides.vocal_min_strength is not None:
        cfg.preset.vocal_min_strength = overrides.vocal_min_strength
    if overrides.guitar_min_strength is not None:
        cfg.preset.guitar_min_strength = overrides.guitar_min_strength
    if overrides.ride_cymbal_mode == "full_kat":
        cfg.preset.drum_mapping["ride"] = "kat"
    elif overrides.ride_cymbal_mode in ("accents_and_solo", "ignore"):
        cfg.preset.drum_mapping["ride"] = "ignore"
    return cfg