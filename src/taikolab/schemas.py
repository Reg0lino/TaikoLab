"""Strict data contracts connecting ingest, arranger, exporters, and UI."""

from __future__ import annotations

from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DrumClass = Literal["kick", "snare", "hihat", "tom", "crash", "ride", "unknown"]
DifficultyLevel = Literal["easy", "normal", "hard", "oni", "ura"]
FollowMode = Literal["auto", "drums", "vocals", "guitar", "hybrid", "chaos_roll"]
TaikoTarget = Literal["don", "kat", "big_don", "big_kat", "ignore"]


class TaikoNote(IntEnum):
    REST = 0
    DON = 1
    KAT = 2
    BIG_DON = 3
    BIG_KAT = 4
    DRUMROLL = 5
    BIG_DRUMROLL = 6
    BALLOON = 7
    END = 8


# ---------------------------------------------------------------------------
# Stage 1 -> Stage 2 Contract: Cached Audio / Tab Analysis (analysis.json)
# ---------------------------------------------------------------------------


class StemPaths(BaseModel):
    drums: str | None = None
    vocals: str | None = None
    bass: str | None = None
    instruments: str | None = None


class BeatEvent(BaseModel):
    time_sec: float = Field(ge=0.0)
    is_downbeat: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class TempoRegion(BaseModel):
    start_sec: float = Field(ge=0.0)
    end_sec: float = Field(gt=0.0)
    bpm: float = Field(gt=20.0, lt=500.0)
    numerator: int = Field(default=4, ge=1, le=32)
    denominator: int = Field(default=4, ge=1, le=32)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class DrumHit(BaseModel):
    time_sec: float = Field(ge=0.0)
    drum_class: DrumClass
    velocity: float = Field(default=0.8, ge=0.0, le=1.0)
    source: Literal["adtof", "dsp", "hybrid", "tab"] = "hybrid"


class OnsetEvent(BaseModel):
    time_sec: float = Field(ge=0.0)
    strength: float = Field(default=0.8, ge=0.0, le=1.0)
    duration_sec: float = Field(default=0.0, ge=0.0)
    pitch_hz: float | None = Field(default=None, ge=0.0)


class EnergyPoint(BaseModel):
    time_sec: float = Field(ge=0.0)
    drums_rms: float = Field(default=0.0, ge=0.0)
    vocals_rms: float = Field(default=0.0, ge=0.0)
    guitar_rms: float = Field(default=0.0, ge=0.0)


class SongAnalysis(BaseModel):
    schema_version: str = "1.0"
    song_id: str
    title: str
    artist: str = "Unknown"
    audio_path: str
    duration_sec: float = Field(gt=0.0)
    stems: StemPaths = Field(default_factory=StemPaths)
    beats: list[BeatEvent] = Field(default_factory=list)
    tempo_regions: list[TempoRegion] = Field(default_factory=list)
    drum_hits: list[DrumHit] = Field(default_factory=list)
    vocal_events: list[OnsetEvent] = Field(default_factory=list)
    guitar_events: list[OnsetEvent] = Field(default_factory=list)
    energy_curve: list[EnergyPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# User / Director's Console Overrides (overrides.json)
# ---------------------------------------------------------------------------


class TempoAnchor(BaseModel):
    time_sec: float = Field(ge=0.0)
    bpm: float | None = Field(default=None, gt=20.0, lt=500.0)
    bpm_multiplier: float = Field(default=1.0, gt=0.1, le=4.0)
    numerator: int = Field(default=4, ge=1, le=32)
    denominator: int = Field(default=4, ge=1, le=32)


class SectionOverride(BaseModel):
    start_sec: float = Field(ge=0.0)
    end_sec: float = Field(gt=0.0)
    label: str = ""
    follow_mode: FollowMode = "auto"
    bpm: float | None = Field(default=None, gt=20.0, lt=500.0)
    bpm_multiplier: float = Field(default=1.0, gt=0.1, le=4.0)
    offset_shift_ms: float = Field(default=0.0, ge=-2000.0, le=2000.0)
    numerator: int | None = Field(default=None, ge=1, le=32)
    denominator: int | None = Field(default=None, ge=1, le=32)
    drum_sensitivity: float = Field(default=1.0, ge=0.1, le=3.0)
    vocal_sensitivity: float = Field(default=1.0, ge=0.1, le=3.0)
    enable_big_notes: bool = True
    fold_blasts_to_rolls: bool | None = None
    max_nps: float | None = Field(default=None, gt=0.5, le=30.0)

    @model_validator(mode="after")
    def _check_time_range(self) -> SectionOverride:
        if self.end_sec <= self.start_sec:
            raise ValueError("SectionOverride end_sec must be greater than start_sec")
        return self


class DownbeatMarker(BaseModel):
    time_sec: float = Field(ge=0.0)
    numerator: int | None = Field(default=None, ge=1, le=32)
    denominator: int | None = Field(default=None, ge=1, le=32)
    locked: bool = True


class DifficultyRuleOverride(BaseModel):
    follow_mode: FollowMode | None = None
    max_nps: float | None = Field(default=None, gt=0.5, le=30.0)
    star_rating: int | None = Field(default=None, ge=1, le=12)
    density_scale: float | None = Field(default=None, ge=0.5, le=1.5)
    phrase_variation: float | None = Field(default=None, ge=0.0, le=1.0)


class ChartControlSettings(BaseModel):
    target_highway_speed: float | None = Field(default=None, ge=70.0, le=160.0)
    big_note_threshold: float | None = Field(default=None, ge=0.6, le=0.98)
    quiet_guitar_rms_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    quiet_guitar_behavior: Literal["downbeat", "empty"] | None = None
    fill_rolls_by_course: dict[DifficultyLevel, int] = Field(default_factory=dict)
    # Read older saved settings that used one cap for every course.
    fill_rolls_per_course: int | None = Field(default=None, ge=0, le=3)
    difficulty_overrides: dict[DifficultyLevel, DifficultyRuleOverride] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _validate_course_roll_caps(self) -> ChartControlSettings:
        for difficulty, cap in self.fill_rolls_by_course.items():
            if not 0 <= cap <= 3:
                raise ValueError(
                    f"Fill-roll cap for {difficulty} must be between 0 and 3"
                )
        return self


class SongOverrides(ChartControlSettings):
    schema_version: str = "1.0"
    song_id: str
    title: str | None = None
    artist: str | None = None
    preset: str = "punk_gallop"
    global_offset_ms: float | None = None
    tempo_anchors: list[TempoAnchor] = Field(default_factory=list)
    sections: list[SectionOverride] = Field(default_factory=list)
    note_edits: dict[str, int] = Field(default_factory=dict)
    custom_downbeats: list[DownbeatMarker] = Field(default_factory=list)
    tap_latency_ms: float = Field(default=0.0, ge=-250.0, le=250.0)


# ---------------------------------------------------------------------------
# Stage 2 -> Stage 3 Contract: Quantized Taiko Chart IR
# ---------------------------------------------------------------------------


class ChartMeasure(BaseModel):
    measure_idx: int = Field(ge=0)
    start_sec: float = Field(ge=0.0)
    end_sec: float = Field(gt=0.0)
    bpm: float = Field(gt=20.0, lt=500.0)
    numerator: int = Field(default=4, ge=1, le=32)
    denominator: int = Field(default=4, ge=1, le=32)
    scroll: float = Field(default=1.0, gt=0.0, le=10.0)
    notes: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_measure(self) -> ChartMeasure:
        if self.end_sec <= self.start_sec:
            raise ValueError("ChartMeasure end_sec must be greater than start_sec")
        if len(self.notes) == 0:
            raise ValueError("ChartMeasure notes grid must not be empty")
        valid_values = {n.value for n in TaikoNote}
        for val in self.notes:
            if val not in valid_values:
                raise ValueError(f"Invalid Taiko note code: {val}")
        return self


class ChartDifficulty(BaseModel):
    difficulty: DifficultyLevel
    level_stars: int = Field(default=5, ge=1, le=12)
    measures: list[ChartMeasure] = Field(default_factory=list)
    balloon_counts: list[int] = Field(default_factory=list)


class TaikoChart(BaseModel):
    title: str
    subtitle: str = ""
    wave_filename: str
    offset_sec: float = 0.0
    demo_start_sec: float = 0.0
    base_bpm: float = Field(gt=20.0, lt=500.0)
    genre: str = "Punk / Hardcore"
    courses: list[ChartDifficulty] = Field(default_factory=list)