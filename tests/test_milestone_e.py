"""Tests for kick+snare flam merger, cymbal sensitivity control, and layout centering."""

from __future__ import annotations

from taikolab.arranger.mapper_drums import _merge_flam_drum_hits, map_drum_hits
from taikolab.arranger.quantizer import MeasureSlot
from taikolab.arranger.section_builder import MeasurePlan
from taikolab.config import load_config
from taikolab.schemas import DrumHit, TaikoNote


def test_merge_flam_drum_hits_absorbs_lazy_pre_snare_kick() -> None:
    # Kick at 1.000s followed 45ms later by snare at 1.045s (flam)
    hits = [
        DrumHit(time_sec=1.000, drum_class="kick", velocity=0.85, source="tab"),
        DrumHit(time_sec=1.045, drum_class="snare", velocity=0.90, source="tab"),
        DrumHit(time_sec=2.000, drum_class="kick", velocity=0.90, source="tab"),
    ]
    merged = _merge_flam_drum_hits(hits, flam_threshold_sec=0.065)
    # The flam should resolve into a single hit on the snare beat (1.045s)
    assert len(merged) == 2
    assert merged[0].drum_class == "snare"
    assert merged[0].time_sec == 1.045
    assert merged[1].time_sec == 2.000


def test_cymbal_sensitivity_zero_skips_hihat_rescue() -> None:
    cfg = load_config("taiko_auto")
    slots = [MeasureSlot(measure_idx=0, start_sec=0.0, end_sec=2.0, bpm=120.0, numerator=4, denominator=4, grid_size=16)]
    # Normal sensitivity (1.0) rescues the hi-hat
    plan_on = [MeasurePlan(measure_idx=0, follow_mode="drums", difficulty="hard", cymbal_sensitivity=1.0)]
    plan_off = [MeasurePlan(measure_idx=0, follow_mode="drums", difficulty="hard", cymbal_sensitivity=0.0)]

    hits = [
        DrumHit(time_sec=0.50, drum_class="hihat", velocity=0.85, source="tab"),
    ]
    placed_on = map_drum_hits(hits, slots, plan_on, cfg)
    placed_off = map_drum_hits(hits, slots, plan_off, cfg)
    assert len(placed_on) == 1
    assert len(placed_off) == 0