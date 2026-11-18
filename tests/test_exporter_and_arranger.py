import re
from pathlib import Path

from taikolab.arranger.engine import arrange_song
from taikolab.config import load_config
from taikolab.exporters.diagnostics import analyze_course, format_chart_report
from taikolab.exporters.tja_writer import measure_duration_sec, render_tja_text, write_tja_file
from taikolab.schemas import BeatEvent, DrumHit, OnsetEvent, SongAnalysis, SongOverrides, TaikoNote, TempoRegion


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