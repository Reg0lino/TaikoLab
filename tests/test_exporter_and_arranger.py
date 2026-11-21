import re
from pathlib import Path

import pytest

from taikolab.arranger.engine import arrange_song
from taikolab.config import load_config
from taikolab.exporters.diagnostics import analyze_course, format_chart_report
from taikolab.exporters.tja_writer import (
    measure_duration_sec,
    render_course_tja,
    render_tja_text,
    write_tja_file,
)
from taikolab.schemas import (
    BeatEvent,
    ChartDifficulty,
    ChartMeasure,
    DifficultyRuleOverride,
    DrumHit,
    EnergyPoint,
    OnsetEvent,
    SectionOverride,
    SongAnalysis,
    SongOverrides,
    TaikoChart,
    TaikoNote,
    TempoRegion,
)


def _make_byob_like_analysis() -> SongAnalysis:
    """Synthetic B.Y.O.B.-style test: 6/8 heavy ~103 BPM then 4/4 thrash ~206 BPM."""
    beats: list[BeatEvent] = []
    drums: list[DrumHit] = []
    guitars: list[OnsetEvent] = []

    t = 1.25
    bar_68 = 240.0 * 6 / (8 * 103.0)
    for _ in range(4):
        beats.append(BeatEvent(time_sec=t, is_downbeat=True))
        beats.append(BeatEvent(time_sec=t + bar_68 * 0.5, is_downbeat=False))
        drums.append(DrumHit(time_sec=t, drum_class="kick", velocity=0.95))
        drums.append(DrumHit(time_sec=t, drum_class="crash", velocity=0.95))
        guitars.append(OnsetEvent(time_sec=t, strength=0.92))
        drums.append(DrumHit(time_sec=t + bar_68 * 0.25, drum_class="snare", velocity=0.85))
        drums.append(DrumHit(time_sec=t + bar_68 * 0.5, drum_class="kick", velocity=0.90))
        drums.append(DrumHit(time_sec=t + bar_68 * 0.75, drum_class="snare", velocity=0.90))
        t += bar_68

    section2_start = t
    bar_44 = 240.0 * 4 / (4 * 206.0)
    for _ in range(8):
        for b in range(4):
            bt = t + b * (bar_44 / 4.0)
            beats.append(BeatEvent(time_sec=bt, is_downbeat=(b == 0)))
            if b % 2 == 0:
                drums.append(DrumHit(time_sec=bt, drum_class="kick", velocity=0.9))
            else:
                drums.append(DrumHit(time_sec=bt, drum_class="snare", velocity=0.95))
            drums.append(DrumHit(time_sec=bt + (bar_44 / 8.0), drum_class="kick", velocity=0.75))
        t += bar_44

    return SongAnalysis(
        song_id="byob_synth",
        title="B.Y.O.B. (Synthetic)",
        artist="System of a Down",
        audio_path="audio.ogg",
        duration_sec=t + 1.0,
        beats=beats,
        tempo_regions=[
            TempoRegion(start_sec=0.0, end_sec=section2_start, bpm=103.0, numerator=6, denominator=8),
            TempoRegion(start_sec=section2_start, end_sec=t + 1.0, bpm=206.0, numerator=4, denominator=4),
        ],
        drum_hits=drums,
        guitar_events=guitars,
    )


def _make_guitar_energy_analysis(include_energy: bool = True) -> SongAnalysis:
    beats = [
        BeatEvent(time_sec=i * 0.5, is_downbeat=(i % 4 == 0))
        for i in range(12)
    ]
    guitar_events = [
        OnsetEvent(time_sec=time_sec, strength=0.8)
        for time_sec in (0.25, 0.75, 1.25, 1.75, 2.25, 2.75, 3.25, 3.75)
    ]
    energy_curve = (
        [
            EnergyPoint(time_sec=i * 0.2, guitar_rms=0.1 if i < 10 else 0.5)
            for i in range(30)
        ]
        if include_energy
        else []
    )
    return SongAnalysis(
        song_id="guitar_energy",
        title="Guitar Energy",
        audio_path="audio.wav",
        duration_sec=6.0,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=6.0, bpm=120.0, numerator=4, denominator=4)],
        guitar_events=guitar_events,
        energy_curve=energy_curve,
    )


def _make_repeated_ride_analysis() -> SongAnalysis:
    beats = []
    drums = []
    bar_duration = 1.2
    for measure_idx in range(8):
        start = measure_idx * bar_duration
        for beat_idx in range(4):
            beat_time = start + beat_idx * 0.3
            beats.append(BeatEvent(time_sec=beat_time, is_downbeat=beat_idx == 0))
            drums.append(DrumHit(time_sec=beat_time, drum_class="kick", velocity=0.8))
        snare_beat = measure_idx if measure_idx < 4 else 2
        drums.append(
            DrumHit(time_sec=start + (snare_beat % 4) * 0.3, drum_class="snare", velocity=0.8)
        )
        for offbeat in (0.15, 0.45, 0.75, 1.05):
            drums.append(DrumHit(time_sec=start + offbeat, drum_class="ride", velocity=0.8))

    return SongAnalysis(
        song_id="repeated_ride",
        title="Repeated Ride",
        audio_path="audio.wav",
        duration_sec=8 * bar_duration,
        beats=beats,
        tempo_regions=[
            TempoRegion(start_sec=0.0, end_sec=8 * bar_duration, bpm=200.0, numerator=4, denominator=4)
        ],
        drum_hits=drums,
    )


def _make_sparse_fill_analysis() -> SongAnalysis:
    beats = []
    drums = []
    bar_duration = 1.2
    for measure_idx in range(13):
        start = measure_idx * bar_duration
        for beat_idx in range(4):
            beat_time = start + beat_idx * 0.3
            beats.append(BeatEvent(time_sec=beat_time, is_downbeat=beat_idx == 0))
            drums.append(DrumHit(time_sec=beat_time, drum_class="kick", velocity=0.8))
        if measure_idx in (0, 4, 8, 12):
            for hit_idx in range(6):
                drums.append(
                    DrumHit(
                        time_sec=start + 0.675 + hit_idx * 0.075,
                        drum_class="snare",
                        velocity=0.8,
                    )
                )

    return SongAnalysis(
        song_id="sparse_fills",
        title="Sparse Fills",
        audio_path="audio.wav",
        duration_sec=13 * bar_duration,
        beats=beats,
        tempo_regions=[
            TempoRegion(start_sec=0.0, end_sec=13 * bar_duration, bpm=200.0, numerator=4, denominator=4)
        ],
        drum_hits=drums,
    )


def test_byob_synthetic_meter_and_zero_drift(tmp_path: Path):
    cfg = load_config("punk_gallop")
    analysis = _make_byob_like_analysis()
    chart = arrange_song(analysis, cfg)

    tja_text = render_tja_text(chart, bpm_deadzone=cfg.settings.smooth_bpm_deadzone)
    assert "OFFSET:-1.250" in tja_text
    assert "#MEASURE 6/8" in tja_text
    assert "#MEASURE 4/4" in tja_text
    assert "#BPMCHANGE" in tja_text

    out_file = write_tja_file(chart, tmp_path / "byob.tja")
    assert out_file.exists()

    oni = [c for c in chart.courses if c.difficulty == "oni"][0]
    oni_block = tja_text.split("COURSE:Oni")[1].split("#END")[0]

    sim_time = analysis.beats[0].time_sec
    cur_bpm = chart.base_bpm
    cur_num, cur_den = 4, 4
    for line in oni_block.splitlines():
        if line.startswith("#BPMCHANGE "):
            cur_bpm = float(line.split()[1])
        elif line.startswith("#MEASURE "):
            n, d = line.split()[1].split("/")
            cur_num, cur_den = int(n), int(d)
        elif line.endswith(","):
            sim_time += measure_duration_sec(cur_num, cur_den, cur_bpm)

    target_end = oni.measures[-1].end_sec
    assert abs(sim_time - target_end) < 0.015, f"Drift too large: sim={sim_time}, target={target_end}"

    report = format_chart_report(chart)
    assert "ONI" in report


def test_short_one_beat_pickup_is_not_merged_with_following_measure():
    pickup_duration = 60.0 / 113.27
    regular_bar_duration = 240.0 / 112.37
    second_measure_start = pickup_duration
    third_measure_start = second_measure_start + regular_bar_duration
    duration = third_measure_start + regular_bar_duration
    beats = [
        BeatEvent(time_sec=0.0, is_downbeat=True),
        BeatEvent(time_sec=second_measure_start, is_downbeat=True),
    ]
    beats.extend(
        BeatEvent(time_sec=second_measure_start + beat * regular_bar_duration / 4)
        for beat in range(1, 4)
    )
    beats.append(BeatEvent(time_sec=third_measure_start, is_downbeat=True))
    beats.extend(
        BeatEvent(time_sec=third_measure_start + beat * regular_bar_duration / 4)
        for beat in range(1, 4)
    )
    analysis = SongAnalysis(
        song_id="short_pickup",
        title="Short Pickup",
        audio_path="audio.wav",
        duration_sec=duration,
        beats=beats,
        tempo_regions=[
            TempoRegion(
                start_sec=0.0,
                end_sec=second_measure_start,
                bpm=113.27,
                numerator=1,
                denominator=4,
            ),
            TempoRegion(
                start_sec=second_measure_start,
                end_sec=duration,
                bpm=112.37,
                numerator=4,
                denominator=4,
            ),
        ],
    )

    chart = arrange_song(analysis, load_config("punk_gallop"))
    text = render_tja_text(chart)

    for course in chart.courses:
        assert course.measures[0].numerator == 1
        assert course.measures[0].denominator == 4
        assert course.measures[0].end_sec == pytest.approx(second_measure_start)
        assert course.measures[1].numerator == 4
    assert "#MEASURE 1/4" in text
    assert "#MEASURE 4/4" in text


def test_quiet_guitar_measure_becomes_one_downbeat_on_all_difficulties():
    cfg = load_config("alt_rock")
    chart = arrange_song(_make_guitar_energy_analysis(), cfg)

    for course in chart.courses:
        quiet_measure = course.measures[0]
        quiet_notes = [idx for idx, note in enumerate(quiet_measure.notes) if note != TaikoNote.REST]
        assert quiet_notes == [0]
        assert quiet_measure.notes[0] == TaikoNote.DON

        louder_measure = course.measures[1]
        assert sum(note != TaikoNote.REST for note in louder_measure.notes) >= 3


def test_quiet_guitar_controls_can_leave_the_intro_empty():
    analysis = _make_guitar_energy_analysis()
    chart = arrange_song(
        analysis,
        load_config("taiko_auto"),
        overrides=SongOverrides(
            song_id=analysis.song_id,
            quiet_guitar_rms_threshold=0.2,
            quiet_guitar_behavior="empty",
        ),
    )

    assert all(
        all(note == TaikoNote.REST.value for note in course.measures[0].notes)
        for course in chart.courses
    )


def test_missing_energy_curve_preserves_existing_guitar_arrangement():
    cfg = load_config("alt_rock")
    chart = arrange_song(_make_guitar_energy_analysis(include_energy=False), cfg)

    for course in chart.courses:
        assert sum(note != TaikoNote.REST for note in course.measures[0].notes) > 1


def test_zero_guitar_energy_does_not_treat_missing_stem_as_quiet_music():
    cfg = load_config("alt_rock")
    analysis = _make_guitar_energy_analysis()
    analysis.energy_curve = [
        EnergyPoint(time_sec=i * 0.2, guitar_rms=0.0)
        for i in range(30)
    ]
    chart = arrange_song(analysis, cfg)

    for course in chart.courses:
        assert sum(note != TaikoNote.REST for note in course.measures[0].notes) > 1


def test_tja_writer_formats_zero_offset_without_negative_sign():
    chart = TaikoChart(title="Zero Offset", wave_filename="audio.wav", base_bpm=120.0)

    assert "OFFSET:0.000" in render_tja_text(chart)


def test_tja_writer_shortens_next_measure_when_playback_is_late():
    durations = [1.0, *([0.99] * 6)]
    start = 0.0
    measures = []
    for idx, duration in enumerate(durations):
        measures.append(
            ChartMeasure(
                measure_idx=idx,
                start_sec=start,
                end_sec=start + duration,
                bpm=240.0,
                notes=[0] * 16,
            )
        )
        start += duration

    lines = render_course_tja(
        ChartDifficulty(difficulty="hard", measures=measures),
        initial_bpm=240.0,
        bpm_deadzone=3.0,
    )
    active_bpm = 240.0
    measure_bpms = []
    for line in lines:
        if line.startswith("#BPMCHANGE "):
            active_bpm = float(line.split()[1])
        elif line.endswith(","):
            measure_bpms.append(active_bpm)

    assert measure_bpms[:2] == [240.0, 240.0]
    assert measure_bpms[2] > 240.0


def test_tja_writer_carries_drift_across_meter_changes():
    durations = [1.0, 0.99, 0.70]
    meters = [(4, 4), (4, 4), (3, 4)]
    start = 0.0
    measures = []
    for idx, (duration, (numerator, denominator)) in enumerate(zip(durations, meters)):
        measures.append(
            ChartMeasure(
                measure_idx=idx,
                start_sec=start,
                end_sec=start + duration,
                bpm=240.0,
                numerator=numerator,
                denominator=denominator,
                notes=[0] * 16,
            )
        )
        start += duration

    lines = render_course_tja(
        ChartDifficulty(difficulty="hard", measures=measures),
        initial_bpm=240.0,
        bpm_deadzone=3.0,
    )
    measure_bpms = []
    active_bpm = 240.0
    for line in lines:
        if line.startswith("#BPMCHANGE "):
            active_bpm = float(line.split()[1])
        elif line.endswith(","):
            measure_bpms.append(active_bpm)

    assert measure_bpms[2] == 258.065


def test_tja_writer_rejects_excessive_timing_drift():
    durations = [1.0, *([0.3] * 3)]
    start = 0.0
    measures = []
    for idx, duration in enumerate(durations):
        measures.append(
            ChartMeasure(
                measure_idx=idx,
                start_sec=start,
                end_sec=start + duration,
                bpm=240.0,
                notes=[0] * 16,
            )
        )
        start += duration

    with pytest.raises(ValueError, match="drifts"):
        render_course_tja(
            ChartDifficulty(difficulty="hard", measures=measures),
            initial_bpm=240.0,
            bpm_deadzone=10.0,
        )


def test_tja_writer_emits_sub_deadzone_bpm_changes_to_limit_drift():
    durations = [measure_duration_sec(4, 4, bpm) for bpm in [90.0, *([89.0] * 12)]]
    start = 0.0
    measures = []
    for idx, duration in enumerate(durations):
        measures.append(
            ChartMeasure(
                measure_idx=idx,
                start_sec=start,
                end_sec=start + duration,
                bpm=90.0,
                notes=[0] * 16,
            )
        )
        start += duration

    lines = render_course_tja(
        ChartDifficulty(difficulty="hard", measures=measures),
        initial_bpm=90.0,
        bpm_deadzone=2.5,
    )

    bpm_changes = [line for line in lines if line.startswith("#BPMCHANGE ")]
    assert len(bpm_changes) > 1


def test_tja_writer_normalizes_highway_speed_without_changing_timing():
    durations = [measure_duration_sec(4, 4, bpm) for bpm in (120.0, 240.0)]
    measures = []
    start = 0.0
    for idx, duration in enumerate(durations):
        measures.append(
            ChartMeasure(
                measure_idx=idx,
                start_sec=start,
                end_sec=start + duration,
                bpm=120.0 if idx == 0 else 240.0,
                notes=[0] * 16,
            )
        )
        start += duration

    course = ChartDifficulty(difficulty="hard", measures=measures)
    plain_lines = render_course_tja(course, initial_bpm=120.0)
    normalized_lines = render_course_tja(
        course,
        initial_bpm=120.0,
        normalize_scroll=True,
        target_highway_speed=101.343,
    )

    plain_timing = [line for line in plain_lines if line.startswith(("#MEASURE", "#BPMCHANGE"))]
    normalized_timing = [
        line for line in normalized_lines if line.startswith(("#MEASURE", "#BPMCHANGE"))
    ]
    assert normalized_timing == plain_timing

    bpm = 120.0
    scroll = 0.5
    speeds = []
    for line in normalized_lines:
        if line.startswith("#BPMCHANGE "):
            bpm = float(line.split()[1])
        elif line.startswith("#SCROLL "):
            scroll = float(line.split()[1])
        elif line.endswith(","):
            speeds.append(bpm * scroll)

    assert speeds == pytest.approx([101.343, 101.343], abs=0.1)


def test_repeated_phrases_use_varying_midi_ride_accents_only_on_hard_and_oni():
    cfg = load_config("punk_gallop")
    chart = arrange_song(_make_repeated_ride_analysis(), cfg)
    courses = {course.difficulty: course for course in chart.courses}
    hard = courses["hard"]
    oni = courses["oni"]

    hard_ride_measures = [
        measure
        for measure in hard.measures
        if TaikoNote.KAT.value in measure.notes
    ]
    assert hard_ride_measures
    assert min(measure.measure_idx for measure in hard_ride_measures) >= 4
    assert len({tuple(i for i, note in enumerate(m.notes) if note == TaikoNote.KAT) for m in hard_ride_measures}) > 1
    assert any(TaikoNote.KAT.value in measure.notes for measure in oni.measures[4:])
    assert len(
        {
            tuple(i for i, note in enumerate(m.notes) if note == TaikoNote.KAT)
            for m in oni.measures[4:]
        }
    ) > 1
    assert all(
        TaikoNote.DRUMROLL.value not in measure.notes
        for course in chart.courses
        for measure in course.measures
    )
    assert [m.start_sec for m in hard.measures] == [m.start_sec for m in oni.measures]
    assert [m.end_sec for m in hard.measures] == [m.end_sec for m in oni.measures]


def test_oni_repeat_variation_slider_changes_colors_without_moving_notes():
    analysis = _make_repeated_ride_analysis()
    cfg = load_config("taiko_auto")
    baseline = arrange_song(analysis, cfg)
    varied = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(
            song_id=analysis.song_id,
            difficulty_overrides={
                "oni": DifficultyRuleOverride(phrase_variation=1.0)
            },
        ),
    )
    baseline_oni = next(course for course in baseline.courses if course.difficulty == "oni")
    varied_oni = next(course for course in varied.courses if course.difficulty == "oni")

    assert baseline_oni.measures[4].notes == varied_oni.measures[4].notes
    assert any(
        baseline_oni.measures[idx].notes != varied_oni.measures[idx].notes
        for idx in (5, 6, 7)
    )
    for idx in range(len(baseline_oni.measures)):
        assert [
            note != TaikoNote.REST.value
            for note in baseline_oni.measures[idx].notes
        ] == [
            note != TaikoNote.REST.value
            for note in varied_oni.measures[idx].notes
        ]


def test_density_slider_reduces_the_course_note_limit():
    analysis = _make_sparse_fill_analysis()
    cfg = load_config("taiko_auto")
    baseline = arrange_song(analysis, cfg)
    reduced = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(
            song_id=analysis.song_id,
            fill_rolls_per_course=0,
            difficulty_overrides={
                "normal": DifficultyRuleOverride(density_scale=0.5)
            },
        ),
    )
    baseline_normal = next(course for course in baseline.courses if course.difficulty == "normal")
    reduced_normal = next(course for course in reduced.courses if course.difficulty == "normal")

    assert sum(note != 0 for measure in reduced_normal.measures for note in measure.notes) < sum(
        note != 0 for measure in baseline_normal.measures for note in measure.notes
    )


def test_easy_and_normal_get_at_most_three_midi_fill_rolls_and_easy_pauses():
    cfg = load_config("punk_gallop")
    chart = arrange_song(_make_sparse_fill_analysis(), cfg)
    courses = {course.difficulty: course for course in chart.courses}
    easy_rolls = [m for m in courses["easy"].measures if TaikoNote.DRUMROLL.value in m.notes]
    normal_rolls = [m for m in courses["normal"].measures if TaikoNote.DRUMROLL.value in m.notes]
    hard_rolls = [m for m in courses["hard"].measures if TaikoNote.DRUMROLL.value in m.notes]
    oni_rolls = [m for m in courses["oni"].measures if TaikoNote.DRUMROLL.value in m.notes]

    assert len(easy_rolls) == 3
    assert len(normal_rolls) == 3
    assert not hard_rolls
    assert not oni_rolls
    assert [m.measure_idx for m in normal_rolls] == [0, 8, 12]
    assert all(
        sum(note == TaikoNote.DRUMROLL.value for note in m.notes)
        == sum(note == TaikoNote.END.value for note in m.notes)
        == 1
        for m in easy_rolls + normal_rolls
    )
    assert any(not any(m.notes) for m in courses["easy"].measures)
    assert [m.start_sec for m in courses["easy"].measures] == [m.start_sec for m in courses["normal"].measures]
    assert [m.end_sec for m in courses["easy"].measures] == [m.end_sec for m in courses["normal"].measures]


def test_fill_roll_control_creates_capped_game_rolls_at_measure_ends():
    analysis = _make_sparse_fill_analysis()
    chart = arrange_song(
        analysis,
        load_config("taiko_auto"),
        overrides=SongOverrides(
            song_id=analysis.song_id,
            fill_rolls_per_course=1,
        ),
    )

    for course in chart.courses:
        roll_measures = [
            measure
            for measure in course.measures
            if TaikoNote.DRUMROLL.value in measure.notes
        ]
        assert len(roll_measures) == 1
        measure = roll_measures[0]
        roll_end_slot = measure.notes.index(TaikoNote.END.value)
        assert roll_end_slot >= int(len(measure.notes) * 0.65)

        roll_end_time = (
            measure.start_sec
            + roll_end_slot / len(measure.notes) * (measure.end_sec - measure.start_sec)
        )
        next_note_times = [
            other.start_sec
            + slot_idx / len(other.notes) * (other.end_sec - other.start_sec)
            for other in course.measures
            for slot_idx, note in enumerate(other.notes)
            if note in (
                TaikoNote.DON.value,
                TaikoNote.KAT.value,
                TaikoNote.BIG_DON.value,
                TaikoNote.BIG_KAT.value,
            )
            and other.start_sec
            + slot_idx / len(other.notes) * (other.end_sec - other.start_sec)
            > roll_end_time
        ]
        if next_note_times:
            min_gap = 2 * (measure.end_sec - measure.start_sec) / len(measure.notes)
            assert min(next_note_times) - roll_end_time >= min_gap - 1e-6


def test_per_course_fill_roll_control_only_affects_selected_courses():
    chart = arrange_song(
        _make_sparse_fill_analysis(),
        load_config("punk_gallop"),
        overrides=SongOverrides(
            song_id="test-fill-rolls",
            fill_rolls_by_course={"easy": 0, "normal": 0, "hard": 1, "oni": 0},
        ),
    )
    roll_counts = {
        course.difficulty: sum(
            TaikoNote.DRUMROLL.value in measure.notes for measure in course.measures
        )
        for course in chart.courses
    }

    assert roll_counts == {"easy": 0, "normal": 0, "hard": 1, "oni": 0}


def test_california_uber_alles_normal_follows_vocals_hard_follows_drums():
    cfg = load_config("punk_gallop")
    beats = [BeatEvent(time_sec=i * 0.375, is_downbeat=(i % 4 == 0)) for i in range(32)]
    vocals = [OnsetEvent(time_sec=i * 0.5, strength=0.85) for i in range(1, 10)]
    drums = []
    for i in range(32):
        bt = i * 0.375
        drums.append(DrumHit(time_sec=bt, drum_class="kick" if i % 2 == 0 else "snare", velocity=0.9))
        drums.append(DrumHit(time_sec=bt + 0.1875, drum_class="kick", velocity=0.8))

    analysis = SongAnalysis(
        song_id="california",
        title="California Uber Alles (Synth)",
        artist="Dead Kennedys",
        audio_path="audio.ogg",
        duration_sec=13.0,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=13.0, bpm=160.0, numerator=4, denominator=4)],
        drum_hits=drums,
        vocal_events=vocals,
    )
    chart = arrange_song(analysis, cfg)
    normal_stats = analyze_course([c for c in chart.courses if c.difficulty == "normal"][0])
    oni_stats = analyze_course([c for c in chart.courses if c.difficulty == "oni"][0])

    assert normal_stats.total_notes < oni_stats.total_notes
    assert oni_stats.avg_nps <= cfg.hori.max_nps_oni + 0.5


def test_spazz_blast_beat_folds_to_valid_closed_drumrolls():
    cfg = load_config("powerviolence")
    beats = [BeatEvent(time_sec=i * 0.25, is_downbeat=(i % 4 == 0)) for i in range(16)]
    drums = [
        DrumHit(
            time_sec=i * 0.0625,
            drum_class="snare" if i % 2 else "kick",
            velocity=0.92,
        )
        for i in range(60)
    ]
    analysis = SongAnalysis(
        song_id="spazz_synth",
        title="Spazz Test",
        artist="Spazz",
        audio_path="audio.ogg",
        duration_sec=4.5,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=4.5, bpm=240.0, numerator=4, denominator=4)],
        drum_hits=drums,
    )
    chart = arrange_song(analysis, cfg, overrides=SongOverrides(song_id="spazz_synth", preset="powerviolence"))
    tja = render_tja_text(chart)

    oni_stats = analyze_course([c for c in chart.courses if c.difficulty == "oni"][0])
    assert oni_stats.roll_count >= 1

    oni_rows = "".join(re.findall(r"^[0-8]+,$", tja.split("COURSE:Oni")[1], flags=re.MULTILINE))
    assert oni_rows.count(str(TaikoNote.DRUMROLL.value)) == oni_rows.count(str(TaikoNote.END.value))


def test_micro_downbeat_glitch_never_exceeds_bpm_limit():
    cfg = load_config("punk_gallop")
    beats = [
        BeatEvent(time_sec=0.5, is_downbeat=True),
        BeatEvent(time_sec=1.0, is_downbeat=False),
        BeatEvent(time_sec=1.5, is_downbeat=False),
        BeatEvent(time_sec=2.0, is_downbeat=False),
        BeatEvent(time_sec=2.5, is_downbeat=True),
        BeatEvent(time_sec=2.66, is_downbeat=True),
        BeatEvent(time_sec=4.5, is_downbeat=True),
    ]
    analysis = SongAnalysis(
        song_id="glitch_test",
        title="Glitch Test",
        audio_path="audio.ogg",
        duration_sec=4.68,
        beats=beats,
        drum_hits=[DrumHit(time_sec=0.5, drum_class="kick", velocity=0.9)],
    )
    chart = arrange_song(analysis, cfg)
    for c in chart.courses:
        for m in c.measures:
            assert 20.0 < m.bpm <= 480.0
def test_barline_warp_locks_notes_to_beat_slots_and_updates_bpm():
    cfg = load_config("punk_gallop")
    beats = [BeatEvent(time_sec=i * 0.5, is_downbeat=(i % 4 == 0)) for i in range(8)]
    drums = [
        DrumHit(time_sec=0.0, drum_class="kick", velocity=0.9),
        DrumHit(time_sec=1.0, drum_class="snare", velocity=0.9),
        DrumHit(time_sec=2.0, drum_class="kick", velocity=0.9),
        DrumHit(time_sec=3.0, drum_class="snare", velocity=0.9),
    ]
    analysis = SongAnalysis(
        song_id="warp_lock",
        title="Warp Lock",
        audio_path="audio.wav",
        duration_sec=4.0,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=4.0, bpm=120.0, numerator=4, denominator=4)],
        drum_hits=drums,
    )

    # Stretch bar 0 from 2.0s to 2.5s (96 BPM) and shrink bar 1 to 1.5s (160 BPM)
    warped = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(
            song_id="warp_lock",
            barline_times=[0.0, 2.5, 4.0],
        ),
    )
    hard = next(c for c in warped.courses if c.difficulty == "hard")
    assert hard.measures[0].bpm == pytest.approx(96.0, abs=0.1)
    assert hard.measures[1].bpm == pytest.approx(160.0, abs=0.1)
    # Beat 1 and Beat 3 stay locked to slot 0 and slot 8 in both measures
    assert hard.measures[0].notes[0] == TaikoNote.DON.value
    assert hard.measures[0].notes[8] == TaikoNote.KAT.value
    assert hard.measures[1].notes[0] == TaikoNote.DON.value
    assert hard.measures[1].notes[8] == TaikoNote.KAT.value
def test_section_bpm_multiplier_splits_and_merges_measures():
    cfg = load_config("punk_gallop")
    beats = [BeatEvent(time_sec=i * 0.5, is_downbeat=(i % 4 == 0)) for i in range(8)]
    analysis = SongAnalysis(
        song_id="sec_mult",
        title="Section Mult",
        audio_path="audio.wav",
        duration_sec=4.0,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=4.0, bpm=120.0, numerator=4, denominator=4)],
        drum_hits=[DrumHit(time_sec=0.0, drum_class="kick", velocity=0.9)],
    )

    doubled = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(
            song_id="sec_mult",
            sections=[SectionOverride(start_sec=0.0, end_sec=4.0, bpm_multiplier=2.0)],
        ),
    )
    hard_double = next(c for c in doubled.courses if c.difficulty == "hard")
    assert len(hard_double.measures) == 4
    assert hard_double.measures[0].bpm == pytest.approx(240.0, abs=0.5)

    halved = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(
            song_id="sec_mult",
            sections=[SectionOverride(start_sec=0.0, end_sec=4.0, bpm_multiplier=0.5)],
        ),
    )
    hard_half = next(c for c in halved.courses if c.difficulty == "hard")
    assert len(hard_half.measures) == 1
    assert hard_half.measures[0].bpm == pytest.approx(60.0, abs=0.5)


def test_explicit_section_bpm_regrids_section_cleanly():
    cfg = load_config("punk_gallop")
    beats = [BeatEvent(time_sec=i * 0.5, is_downbeat=(i % 4 == 0)) for i in range(8)]
    analysis = SongAnalysis(
        song_id="sec_bpm",
        title="Section BPM",
        audio_path="audio.wav",
        duration_sec=4.0,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=4.0, bpm=120.0, numerator=4, denominator=4)],
        drum_hits=[DrumHit(time_sec=0.0, drum_class="kick", velocity=0.9)],
    )

    chart = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(
            song_id="sec_bpm",
            sections=[SectionOverride(start_sec=0.0, end_sec=4.0, bpm=240.0)],
        ),
    )
    hard = next(c for c in chart.courses if c.difficulty == "hard")
    assert len(hard.measures) == 4
    assert all(m.bpm == pytest.approx(240.0, abs=0.5) for m in hard.measures)
def test_high_tom_and_mixed_fill_shape_produces_kas_not_all_dons():
    cfg = load_config("punk_gallop")
    beats = [BeatEvent(time_sec=i * 0.3, is_downbeat=(i % 4 == 0)) for i in range(8)]
    drums = [
        DrumHit(time_sec=0.0, drum_class="kick", velocity=0.9),
        DrumHit(time_sec=0.3, drum_class="snare", velocity=0.9),
        # Rapid snare + high/low tom fill in second half of bar 0
        DrumHit(time_sec=0.60, drum_class="snare", velocity=0.85, pitch=38),
        DrumHit(time_sec=0.675, drum_class="tom", velocity=0.85, pitch=50),
        DrumHit(time_sec=0.75, drum_class="tom", velocity=0.85, pitch=48),
        DrumHit(time_sec=0.825, drum_class="tom", velocity=0.85, pitch=45),
        DrumHit(time_sec=0.90, drum_class="tom", velocity=0.85, pitch=43),
        DrumHit(time_sec=0.975, drum_class="tom", velocity=0.85, pitch=41),
        DrumHit(time_sec=1.20, drum_class="kick", velocity=0.95),
        DrumHit(time_sec=1.20, drum_class="crash", velocity=0.95, pitch=49),
    ]
    analysis = SongAnalysis(
        song_id="tom_fill",
        title="Tom Fill",
        audio_path="audio.wav",
        duration_sec=2.4,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=2.4, bpm=200.0, numerator=4, denominator=4)],
        drum_hits=drums,
    )
    chart = arrange_song(analysis, cfg)
    hard = next(c for c in chart.courses if c.difficulty == "hard")
    fill_notes = hard.measures[0].notes[8:]
    assert TaikoNote.KAT.value in fill_notes
    assert TaikoNote.DON.value in fill_notes
    assert hard.measures[1].notes[0] == TaikoNote.BIG_KAT.value


def test_mixed_snare_tom_fill_triggers_climax_balloon_before_crash():
    cfg = load_config("punk_gallop")
    beats = [BeatEvent(time_sec=i * 0.4, is_downbeat=(i % 4 == 0)) for i in range(12)]
    drums = [
        DrumHit(time_sec=0.0, drum_class="kick", velocity=0.9),
        # Mixed 0.60s snare + tom fill at slots 8..14 leaving 1/8th clearance before bar 1 crash
        DrumHit(time_sec=0.80, drum_class="snare", velocity=0.9, pitch=38),
        DrumHit(time_sec=0.90, drum_class="tom", velocity=0.9, pitch=48),
        DrumHit(time_sec=1.00, drum_class="snare", velocity=0.9, pitch=38),
        DrumHit(time_sec=1.10, drum_class="tom", velocity=0.9, pitch=45),
        DrumHit(time_sec=1.20, drum_class="tom", velocity=0.9, pitch=43),
        DrumHit(time_sec=1.30, drum_class="snare", velocity=0.9, pitch=38),
        DrumHit(time_sec=1.40, drum_class="tom", velocity=0.9, pitch=41),
        DrumHit(time_sec=1.60, drum_class="kick", velocity=0.95),
        DrumHit(time_sec=1.60, drum_class="crash", velocity=0.95),
    ]
    analysis = SongAnalysis(
        song_id="climax_balloon",
        title="Climax Balloon",
        audio_path="audio.wav",
        duration_sec=4.8,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=4.8, bpm=150.0, numerator=4, denominator=4)],
        drum_hits=drums,
    )
    chart = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(
            song_id="climax_balloon",
            target_highway_speed=240.0,
            fill_rolls_by_course={"easy": 1, "normal": 1, "hard": 1, "oni": 1},
        ),
    )
    hard = next(c for c in chart.courses if c.difficulty == "hard")
    assert TaikoNote.BALLOON.value in hard.measures[0].notes
    assert TaikoNote.END.value in hard.measures[0].notes
    assert len(hard.balloon_counts) == 1
    assert 5 <= hard.balloon_counts[0] <= 22
def test_4bar_repeated_phrases_get_ride_accents_and_protect_downbeats():
    cfg = load_config("punk_gallop")
    bar_dur = 1.6
    beats = []
    drums = []
    for m_idx in range(8):
        start = m_idx * bar_dur
        for b in range(4):
            bt = start + b * 0.4
            beats.append(BeatEvent(time_sec=bt, is_downbeat=(b == 0)))
            drums.append(DrumHit(time_sec=bt, drum_class="kick" if b % 2 == 0 else "snare", velocity=0.85))
        # Add offbeat ride hits and vary bar index inside the 4-bar A-B-C-D motif
        if m_idx % 4 == 1:
            drums.append(DrumHit(time_sec=start + 0.2, drum_class="kick", velocity=0.8))
        if m_idx % 4 == 3:
            drums.append(DrumHit(time_sec=start + 1.0, drum_class="snare", velocity=0.8))
        for off in (0.2, 0.6, 1.0, 1.4):
            drums.append(DrumHit(time_sec=start + off, drum_class="ride", velocity=0.85))

    analysis = SongAnalysis(
        song_id="phrase_4bar",
        title="4-Bar Phrase",
        audio_path="audio.wav",
        duration_sec=8 * bar_dur,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=8 * bar_dur, bpm=150.0, numerator=4, denominator=4)],
        drum_hits=drums,
    )
    chart = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(
            song_id="phrase_4bar",
            difficulty_overrides={"oni": DifficultyRuleOverride(phrase_variation=0.5)},
        ),
    )
    oni = next(c for c in chart.courses if c.difficulty == "oni")
    # First 4-bar phrase stays faithful (only beat hits + bar 1/3 motif)
    assert oni.measures[0].notes[2] == TaikoNote.REST.value
    # Repeated 4-bar phrase (bars 4..7) gains ride accents while keeping beat 1 Don intact
    assert any(
        oni.measures[idx].notes[slot] == TaikoNote.KAT.value
        for idx in range(4, 8)
        for slot in (2, 6, 10, 14)
    )
    assert all(oni.measures[idx].notes[0] == TaikoNote.DON.value for idx in range(8))


def test_balanced_hybrid_weaves_guitar_and_vocal_hooks_into_sparse_drum_pockets():
    cfg = load_config("punk_gallop")
    beats = [BeatEvent(time_sec=i * 0.4, is_downbeat=(i % 4 == 0)) for i in range(8)]
    drums = [
        # Bar 0: 4 quarter drum hits (spare pockets on 8th offbeats)
        DrumHit(time_sec=0.0, drum_class="kick", velocity=0.85),
        DrumHit(time_sec=0.4, drum_class="snare", velocity=0.85),
        DrumHit(time_sec=0.8, drum_class="kick", velocity=0.85),
        DrumHit(time_sec=1.2, drum_class="snare", velocity=0.85),
        # Bar 1: drum dropout (0 hits)
    ]
    guitars = [
        OnsetEvent(time_sec=0.2, strength=0.82),
        OnsetEvent(time_sec=1.0, strength=0.82),
        OnsetEvent(time_sec=1.6, strength=0.85),
        OnsetEvent(time_sec=2.0, strength=0.85),
        OnsetEvent(time_sec=2.4, strength=0.85),
    ]
    vocals = [
        OnsetEvent(time_sec=1.4, strength=0.85),
    ]
    analysis = SongAnalysis(
        song_id="balanced_hooks",
        title="Balanced Hooks",
        audio_path="audio.wav",
        duration_sec=3.2,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=3.2, bpm=150.0, numerator=4, denominator=4)],
        drum_hits=drums,
        guitar_events=guitars,
        vocal_events=vocals,
    )
    chart = arrange_song(analysis, cfg)
    hard = next(c for c in chart.courses if c.difficulty == "hard")
    oni = next(c for c in chart.courses if c.difficulty == "oni")
    # Hard catches the drum-dropout bar 1 via guitar fallback
    assert sum(n != 0 for n in hard.measures[1].notes) >= 2
    # Oni weaves offbeat guitar hooks into bar 0 alongside the 4 drum beats
    assert oni.measures[0].notes[2] != TaikoNote.REST.value
def test_higher_fill_roll_cap_up_to_12_and_dense_tom_groove_balance():
    cfg = load_config("punk_gallop")
    beats = [BeatEvent(time_sec=i * 0.4, is_downbeat=(i % 4 == 0)) for i in range(4)]
    drums = [
        DrumHit(time_sec=i * 0.2, drum_class="tom", velocity=0.85, pitch=43)
        for i in range(8)
    ]
    analysis = SongAnalysis(
        song_id="tom_groove",
        title="Tom Groove",
        audio_path="audio.wav",
        duration_sec=1.6,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=1.6, bpm=150.0, numerator=4, denominator=4)],
        drum_hits=drums,
    )
    chart = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(
            song_id="tom_groove",
            fill_rolls_by_course={"easy": 6, "normal": 6, "hard": 6, "oni": 6},
        ),
    )
    hard = next(c for c in chart.courses if c.difficulty == "hard")
    assert TaikoNote.KAT.value in hard.measures[0].notes
def test_halftime_72bpm_uses_32_slots_so_144bpm_16ths_do_not_collide():
    cfg = load_config("punk_gallop")
    bar_dur = 240.0 / 72.0
    beats = [BeatEvent(time_sec=i * (bar_dur / 4), is_downbeat=(i % 4 == 0)) for i in range(4)]
    # Two rapid hits 0.104s apart (144 BPM 16th note inside a 72 BPM bar)
    drums = [
        DrumHit(time_sec=0.0, drum_class="kick", velocity=0.9),
        DrumHit(time_sec=0.104, drum_class="snare", velocity=0.9),
        DrumHit(time_sec=bar_dur * 0.25, drum_class="snare", velocity=0.9),
    ]
    analysis = SongAnalysis(
        song_id="halftime_72",
        title="Halftime 72",
        audio_path="audio.wav",
        duration_sec=bar_dur,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=bar_dur, bpm=72.0, numerator=4, denominator=4)],
        drum_hits=drums,
    )
    chart = arrange_song(analysis, cfg)
    hard = next(c for c in chart.courses if c.difficulty == "hard")
    assert len(hard.measures[0].notes) == 32
    assert hard.measures[0].notes[0] == TaikoNote.DON.value
    assert hard.measures[0].notes[1] == TaikoNote.KAT.value