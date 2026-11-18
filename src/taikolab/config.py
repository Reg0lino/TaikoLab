"""Configuration models and YAML preset loader for TaikoLab."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from taikolab.schemas import DrumClass, FollowMode, TaikoTarget


class GlobalSettings(BaseModel):
    workspace_dir: str = "workspace"
    opentaiko_songs_dir: str = ""
    global_audio_offset_ms: float = 0.0
    smooth_bpm_deadzone: float = Field(default=2.5, ge=0.0, le=20.0)
    normalize_scroll_on_bpm_change: bool = False
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


def load_config(preset_name: str | None = None, root_dir: Path | None = None) -> AppConfig:
    root = find_project_root(root_dir)
    configs_dir = root / "configs"

    settings = GlobalSettings(**_load_yaml(configs_dir / "default_settings.yaml"))
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