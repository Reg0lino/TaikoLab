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
    DrumHit,
    EnergyPoint,
    OnsetEvent,
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
                        time_sec=start + 0.15 + hit_idx * 0.075,
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


def test_easy_and_normal_get_at_most_three_midi_fill_rolls_and_easy_pauses():
    cfg = load_config("punk_gallop")
    chart = arrange_song(_make_sparse_fill_analysis(), cfg)
    courses = {course.difficulty: course for course in chart.courses}
    easy_rolls = [m for m in courses["easy"].measures if TaikoNote.DRUMROLL.value in m.notes]
    normal_rolls = [m for m in courses["normal"].measures if TaikoNote.DRUMROLL.value in m.notes]

    assert len(easy_rolls) == 3
    assert len(normal_rolls) == 3
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