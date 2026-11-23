"""Tests for hands-over-feet fill rule, stream big-note suppression, and GP 3/4 meter parsing."""

from __future__ import annotations

import zipfile
from pathlib import Path

from taikolab.arranger.mapper_drums import map_drum_hits
from taikolab.arranger.quantizer import MeasureSlot
from taikolab.arranger.section_builder import MeasurePlan
from taikolab.config import load_config
from taikolab.ingest.gp_parser import parse_gp_hints
from taikolab.schemas import DrumHit, TaikoNote


def test_hands_over_feet_suppresses_big_notes_and_keeps_snare_stream_kat() -> None:
    cfg = load_config("taiko_auto")
    slots = [MeasureSlot(measure_idx=0, start_sec=0.0, end_sec=2.0, bpm=120.0, numerator=4, denominator=4, grid_size=16)]
    plans = [MeasurePlan(measure_idx=0, follow_mode="drums", difficulty="hard", enable_big_notes=True)]

    hits = []
    # Kick pedal on beats 2, 3, 4 (slots 4, 8, 12)
    for s in (4, 8, 12):
        hits.append(DrumHit(time_sec=s * 0.125, drum_class="kick", velocity=0.95, source="tab"))
    # Continuous 16th snare roll across slots 4..15
    for s in range(4, 16):
        hits.append(DrumHit(time_sec=s * 0.125, drum_class="snare", velocity=0.92, source="tab", pitch=38))

    placed = map_drum_hits(hits, slots, plans, cfg)
    # Slots 4, 8, 12 had kick + snare, but because s_idx > 0 and in a stream, hands WIN -> regular KAT!
    for s in (4, 8, 12):
        cand = placed[(0, s)]
        assert cand.note_type == TaikoNote.KAT
        assert cand.source == "drum:snare"
    # No big notes anywhere in the 16th fill
    for s in range(4, 16):
        assert placed[(0, s)].note_type in (TaikoNote.DON, TaikoNote.KAT)


def test_gp_parser_extracts_3_4_time_signatures(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<GPIF>
  <MasterBars>
    <MasterBar><Time>4/4</Time></MasterBar>
    <MasterBar><Time>4/4</Time></MasterBar>
    <MasterBar><Time>3/4</Time></MasterBar>
    <MasterBar><Time>3/4</Time></MasterBar>
    <MasterBar><Time>4/4</Time></MasterBar>
  </MasterBars>
</GPIF>"""
    gp_file = tmp_path / "byob_test.gp"
    with zipfile.ZipFile(gp_file, "w") as zf:
        zf.writestr("Content/score.gpif", xml)

    hints = parse_gp_hints(gp_file)
    assert hints.bar_meters[0] == (4, 4)
    assert hints.bar_meters[1] == (4, 4)
    assert hints.bar_meters[2] == (3, 4)
    assert hints.bar_meters[3] == (3, 4)
    assert hints.bar_meters[4] == (4, 4)