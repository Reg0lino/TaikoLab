"""Milestone B tests: GP7+ XML section parsing, GP staging, auto-sections & phrase variety."""

from __future__ import annotations

import zipfile
from pathlib import Path

from taikolab.arranger.quantizer import build_measure_slots
from taikolab.arranger.section_builder import auto_build_sections, build_measure_plans
from taikolab.config import load_config
from taikolab.ingest.gp_parser import parse_gp_hints
from taikolab.schemas import BeatEvent, DrumHit, GpHints, GpSectionMarker, OnsetEvent, SongAnalysis, SongOverrides, TempoRegion
from taikolab.song_setup import find_staged_gp_input, stage_song_inputs


def _make_test_gp(path: Path) -> Path:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<GPIF>
  <Tracks>
    <Track><Name>Lead Vocals</Name></Track>
    <Track><Name>Drums</Name></Track>
  </Tracks>
  <MasterBars>
    <MasterBar><Section><Text>Intro</Text></Section><Bars>0</Bars></MasterBar>
    <MasterBar><Bars>1</Bars></MasterBar>
    <MasterBar><Section><Text>Verse 1</Text></Section><Bars>2</Bars></MasterBar>
    <MasterBar><Section><Text>Chorus</Text></Section><Bars>3</Bars></MasterBar>
  </MasterBars>
  <Bars>
    <Bar id="0"><Voices>0</Voices></Bar>
    <Bar id="1"><Voices>1</Voices></Bar>
    <Bar id="2"><Voices>2</Voices></Bar>
    <Bar id="3"><Voices>3</Voices></Bar>
  </Bars>
  <Voices>
    <Voice id="0"><Beats>0</Beats></Voice>
    <Voice id="1"><Beats>1</Beats></Voice>
    <Voice id="2"><Beats>2</Beats></Voice>
    <Voice id="3"><Beats>3</Beats></Voice>
  </Voices>
  <Beats>
    <Beat id="0"><Properties><Property name="PalmMuted"/></Properties></Beat>
    <Beat id="1"><Notes>10</Notes></Beat>
    <Beat id="2"><Properties><Property name="Tremolo"/></Properties></Beat>
    <Beat id="3"><Notes>11</Notes></Beat>
  </Beats>
  <Notes>
    <Note id="10"><Accent>4</Accent></Note>
    <Note id="11"/>
  </Notes>
</GPIF>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Content/score.gpif", xml)
    return path


def test_parse_gp7_sections_and_articulations(tmp_path: Path) -> None:
    gp_file = _make_test_gp(tmp_path / "song.gp")
    hints = parse_gp_hints(gp_file)
    assert [s.label for s in hints.sections] == ["Intro", "Verse 1", "Chorus"]
    assert [s.role for s in hints.sections] == ["intro", "verse", "chorus"]
    assert hints.palm_mute_bars == [0]
    assert hints.accent_bars == [1]
    assert hints.tremolo_bars == [2]


def test_stage_song_inputs_accepts_optional_gp_source(tmp_path: Path) -> None:
    audio = tmp_path / "track.mp3"
    midi = tmp_path / "track.mid"
    gp = _make_test_gp(tmp_path / "track.gp")
    audio.write_bytes(b"mp3")
    midi.write_bytes(b"mid")

    song_dir = tmp_path / "staged"
    staged_audio, staged_midi = stage_song_inputs(song_dir, audio, midi, gp_source=gp)
    assert staged_audio.name == "audio.mp3"
    assert staged_midi is not None and staged_midi.name == "input.mid"
    staged_gp = find_staged_gp_input(song_dir)
    assert staged_gp is not None and staged_gp.name == "input.gp"


def test_auto_build_sections_uses_gp_markers_and_procedural_variety_rotates_active_blocks() -> None:
    cfg = load_config("punk_gallop")
    beats = []
    for b in range(16):
        beats.append(BeatEvent(time_sec=b * 2.0, is_downbeat=True))
        beats.append(BeatEvent(time_sec=b * 2.0 + 0.5, is_downbeat=False))
        beats.append(BeatEvent(time_sec=b * 2.0 + 1.0, is_downbeat=False))
        beats.append(BeatEvent(time_sec=b * 2.0 + 1.5, is_downbeat=False))

    drums = [DrumHit(time_sec=t, drum_class="kick", velocity=0.9, source="tab") for t in [i * 0.5 for i in range(64)]]
    vocals = [OnsetEvent(time_sec=t, strength=0.8) for t in [i * 0.5 for i in range(64)]]
    analysis = SongAnalysis(
        song_id="gp-test",
        title="GP Test",
        audio_path="audio.wav",
        duration_sec=32.0,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=32.0, bpm=120.0)],
        drum_hits=drums,
        vocal_events=vocals,
        gp_hints=GpHints(
            sections=[
                GpSectionMarker(bar_idx=0, label="Verse 1", role="verse", time_sec=0.0),
                GpSectionMarker(bar_idx=12, label="Chorus", role="chorus", time_sec=24.0),
            ]
        ),
    )
    slots = build_measure_slots(analysis)
    secs = auto_build_sections(analysis, slots)
    assert [s.label for s in secs] == ["Verse 1", "Chorus"]
    assert secs[0].difficulty_rules["normal"].follow_mode == "vocals"
    assert secs[1].difficulty_rules["hard"].follow_mode == "all_balanced"

    ov = SongOverrides(song_id="gp-test", sections=secs, section_phrase_variety=0.85)
    plans = build_measure_plans(slots, analysis, "hard", cfg, overrides=ov)
    verse_modes = {plans[i].follow_mode for i in range(0, 12)}
    assert len(verse_modes) >= 2