"""FastAPI backend for TaikoLab Director's Console UI."""

from __future__ import annotations

import statistics
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from taikolab.arranger.engine import arrange_song
from taikolab.config import load_config
from taikolab.exporters.diagnostics import analyze_course
from taikolab.exporters.tja_writer import export_to_opentaiko, write_tja_file
from taikolab.schemas import DownbeatMarker, SectionOverride, SongAnalysis, SongOverrides

STATIC_DIR = Path(__file__).resolve().parent / "static"


class ArrangeResponse(BaseModel):
    song_id: str
    analysis_summary: dict
    overrides: SongOverrides
    chart: dict
    diagnostics: list[dict]
    exported_path: str | None = None


class SnapTapsRequest(BaseModel):
    taps: list[DownbeatMarker]
    window_ms: float = 45.0
    tap_latency_ms: float = 0.0


class StraightenTapsRequest(BaseModel):
    taps: list[DownbeatMarker]
    tolerance_pct: float = 5.5


class FillGapsRequest(BaseModel):
    taps: list[DownbeatMarker]


def snap_taps_to_transients(
    taps: list[DownbeatMarker],
    analysis: SongAnalysis,
    window_ms: float = 45.0,
    tap_latency_ms: float = 0.0,
) -> tuple[list[DownbeatMarker], int]:
    """Shift taps by human latency compensation, then magnet-snap to nearest drum/guitar hit."""
    candidates: list[tuple[float, float]] = []
    for h in analysis.drum_hits:
        if h.drum_class in ("kick", "snare", "crash"):
            w = 1.15 if h.drum_class in ("kick", "crash") else 1.0
            candidates.append((h.time_sec, h.velocity * w))
    for g in analysis.guitar_events:
        if g.strength >= 0.5:
            candidates.append((g.time_sec, g.strength * 0.75))

    candidates.sort(key=lambda x: x[0])
    cand_times = np.array([c[0] for c in candidates], dtype=np.float64)
    cand_weights = np.array([c[1] for c in candidates], dtype=np.float64)

    win_sec = max(0.005, window_ms / 1000.0)
    shift_sec = tap_latency_ms / 1000.0
    snapped_count = 0
    out: list[DownbeatMarker] = []

    for m in sorted(taps, key=lambda x: x.time_sec):
        t_adj = max(0.0, m.time_sec + shift_sec)
        new_t = t_adj
        if len(cand_times) > 0:
            lo = int(np.searchsorted(cand_times, t_adj - win_sec))
            hi = int(np.searchsorted(cand_times, t_adj + win_sec))
            if hi > lo:
                # Pick strongest transient weighted gently by proximity
                best_idx = lo
                best_score = -1.0
                for idx in range(lo, hi):
                    dist = abs(float(cand_times[idx]) - t_adj)
                    score = float(cand_weights[idx]) * (1.0 - 0.35 * (dist / win_sec))
                    if score > best_score:
                        best_score = score
                        best_idx = idx
                new_t = float(cand_times[best_idx])
                snapped_count += 1

        out.append(
            DownbeatMarker(
                time_sec=round(new_t, 4),
                numerator=m.numerator,
                denominator=m.denominator,
                locked=m.locked,
            )
        )

    return out, snapped_count


def straighten_steady_taps(
    taps: list[DownbeatMarker],
    tolerance_pct: float = 5.5,
) -> list[DownbeatMarker]:
    """Flatten +/-5.5% human tap wobble inside steady runs into constant-BPM bar spacing."""
    sorted_m = sorted(taps, key=lambda x: x.time_sec)
    if len(sorted_m) < 4:
        return sorted_m

    durs = [sorted_m[i + 1].time_sec - sorted_m[i].time_sec for i in range(len(sorted_m) - 1)]
    tol = tolerance_pct / 100.0

    # Group consecutive bars with similar duration (minimum 3 bars)
    groups: list[tuple[int, int]] = []  # (start_bar_idx, end_bar_idx inclusive)
    g_start = 0
    g_durs = [durs[0]]
    for i in range(1, len(durs)):
        med = statistics.median(g_durs)
        if med > 0 and abs(durs[i] - med) / med <= tol:
            g_durs.append(durs[i])
        else:
            if (i - g_start) >= 3:
                groups.append((g_start, i - 1))
            g_start = i
            g_durs = [durs[i]]
    if (len(durs) - g_start) >= 3:
        groups.append((g_start, len(durs) - 1))

    out = [m.model_copy() for m in sorted_m]
    for s_idx, e_idx in groups:
        n_bars = e_idx - s_idx + 1
        t_start = out[s_idx].time_sec
        t_end = out[e_idx + 1].time_sec
        even_dur = (t_end - t_start) / float(n_bars)
        for k in range(1, n_bars):
            out[s_idx + k].time_sec = round(t_start + k * even_dur, 4)

    return out


def fill_missed_double_gaps(taps: list[DownbeatMarker]) -> tuple[list[DownbeatMarker], int]:
    """Detect ~2x bar gaps caused by a skipped Numpad-0 tap and split them cleanly at midpoint."""
    sorted_m = sorted(taps, key=lambda x: x.time_sec)
    if len(sorted_m) < 3:
        return sorted_m, 0

    durs = [sorted_m[i + 1].time_sec - sorted_m[i].time_sec for i in range(len(sorted_m) - 1)]
    med_dur = statistics.median(durs)
    if med_dur < 0.35:
        return sorted_m, 0

    out: list[DownbeatMarker] = [sorted_m[0].model_copy()]
    added = 0
    for i in range(len(durs)):
        cur_dur = durs[i]
        # Local reference around i
        near = durs[max(0, i - 3) : min(len(durs), i + 4)]
        ref = statistics.median([d for d in near if d <= med_dur * 1.65] or [med_dur])
        if 1.75 * ref <= cur_dur <= 2.35 * ref:
            mid_t = 0.5 * (sorted_m[i].time_sec + sorted_m[i + 1].time_sec)
            out.append(
                DownbeatMarker(
                    time_sec=round(mid_t, 4),
                    numerator=sorted_m[i].numerator,
                    denominator=sorted_m[i].denominator,
                )
            )
            added += 1
        out.append(sorted_m[i + 1].model_copy())

    return out, added


def _default_sections_from_analysis(analysis: SongAnalysis) -> list[SectionOverride]:
    if not analysis.tempo_regions:
        return [SectionOverride(start_sec=0.0, end_sec=analysis.duration_sec, label="Full Song", follow_mode="auto")]

    merged: list[SectionOverride] = []
    for reg in analysis.tempo_regions:
        if merged and (reg.end_sec - reg.start_sec) < 4.0:
            merged[-1].end_sec = reg.end_sec
            continue
        merged.append(
            SectionOverride(
                start_sec=round(reg.start_sec, 3),
                end_sec=round(reg.end_sec, 3),
                label=f"Part {len(merged) + 1} ({reg.bpm:.0f} BPM {reg.numerator}/{reg.denominator})",
                follow_mode="auto",
            )
        )
    if merged:
        merged[0].start_sec = 0.0
        merged[-1].end_sec = round(analysis.duration_sec, 3)
    return merged


def _build_chart_payload(song_id: str, overrides: SongOverrides | None = None, export_sim: bool = False) -> ArrangeResponse:
    cfg = load_config(overrides.preset if overrides else None)
    song_dir = cfg.song_dir(song_id)
    analysis_path = song_dir / "analysis.json"
    if not analysis_path.exists():
        raise HTTPException(status_code=404, detail=f"Missing analysis.json for '{song_id}'")

    analysis = SongAnalysis.model_validate_json(analysis_path.read_text(encoding="utf-8"))

    overrides_path = song_dir / "overrides.json"
    if overrides is None:
        if overrides_path.exists():
            overrides = SongOverrides.model_validate_json(overrides_path.read_text(encoding="utf-8"))
        else:
            overrides = SongOverrides(
                song_id=song_id,
                title=analysis.title,
                artist=analysis.artist,
                preset=cfg.preset.name,
                global_offset_ms=cfg.settings.global_audio_offset_ms,
                sections=_default_sections_from_analysis(analysis),
            )
    else:
        overrides_path.write_text(overrides.model_dump_json(indent=2), encoding="utf-8")

    cfg = load_config(overrides.preset or cfg.preset.name)
    chart = arrange_song(analysis, cfg, overrides=overrides)
    tja_path = song_dir / f"{song_id}.tja"
    write_tja_file(
        chart,
        tja_path,
        bpm_deadzone=cfg.settings.smooth_bpm_deadzone,
        normalize_scroll=cfg.settings.normalize_scroll_on_bpm_change,
    )

    exported_str = None
    if export_sim:
        audio_file = song_dir / analysis.audio_path
        copied = export_to_opentaiko(tja_path, audio_file, cfg.settings.opentaiko_songs_dir, folder_name=song_id)
        exported_str = str(copied) if copied else None

    diag = [vars(analyze_course(c)) for c in chart.courses]
    stems_avail = ["mix"]
    if analysis.stems.drums and Path(analysis.stems.drums).exists():
        stems_avail.append("drums")
    if analysis.stems.vocals and Path(analysis.stems.vocals).exists():
        stems_avail.append("vocals")
    if analysis.stems.instruments and Path(analysis.stems.instruments).exists():
        stems_avail.append("instruments")

    return ArrangeResponse(
        song_id=song_id,
        analysis_summary={
            "title": analysis.title,
            "artist": analysis.artist,
            "duration_sec": analysis.duration_sec,
            "audio_path": analysis.audio_path,
            "stems_available": stems_avail,
            "drum_hits": len(analysis.drum_hits),
            "vocal_events": len(analysis.vocal_events),
            "guitar_events": len(analysis.guitar_events),
            "simulator_dir": cfg.settings.opentaiko_songs_dir,
        },
        overrides=overrides,
        chart=chart.model_dump(),
        diagnostics=diag,
        exported_path=exported_str,
    )


def create_app() -> FastAPI:
    app = FastAPI(title="TaikoLab Director's Console")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.get("/api/songs")
    def list_songs():
        cfg = load_config()
        ws = Path(cfg.settings.workspace_dir)
        if not ws.is_absolute():
            ws = cfg.root_dir / ws
        songs = []
        if ws.exists():
            for d in sorted(ws.iterdir()):
                if d.is_dir() and (d / "analysis.json").exists():
                    songs.append({"song_id": d.name, "has_overrides": (d / "overrides.json").exists()})
        return {"songs": songs}

    @app.get("/api/songs/{song_id}")
    def get_song(song_id: str):
        return _build_chart_payload(song_id, overrides=None, export_sim=False)

    @app.post("/api/songs/{song_id}/arrange")
    def arrange_song_api(song_id: str, overrides: SongOverrides):
        return _build_chart_payload(song_id, overrides=overrides, export_sim=False)

    @app.post("/api/songs/{song_id}/export")
    def export_song_api(song_id: str, overrides: SongOverrides):
        return _build_chart_payload(song_id, overrides=overrides, export_sim=True)

    @app.post("/api/songs/{song_id}/taps/snap")
    def snap_taps_api(song_id: str, req: SnapTapsRequest):
        cfg = load_config()
        analysis_path = cfg.song_dir(song_id) / "analysis.json"
        if not analysis_path.exists():
            raise HTTPException(status_code=404, detail="Song not found")
        analysis = SongAnalysis.model_validate_json(analysis_path.read_text(encoding="utf-8"))
        snapped, count = snap_taps_to_transients(
            req.taps, analysis, window_ms=req.window_ms, tap_latency_ms=req.tap_latency_ms
        )
        return {"taps": [m.model_dump() for m in snapped], "snapped_count": count}

    @app.post("/api/songs/{song_id}/taps/straighten")
    def straighten_taps_api(song_id: str, req: StraightenTapsRequest):
        straight = straighten_steady_taps(req.taps, tolerance_pct=req.tolerance_pct)
        return {"taps": [m.model_dump() for m in straight]}

    @app.post("/api/songs/{song_id}/taps/fill-gaps")
    def fill_gaps_api(song_id: str, req: FillGapsRequest):
        filled, added = fill_missed_double_gaps(req.taps)
        return {"taps": [m.model_dump() for m in filled], "added_count": added}

    @app.get("/api/songs/{song_id}/audio")
    def stream_audio(song_id: str, stem: str = Query("mix")):
        cfg = load_config()
        song_dir = cfg.song_dir(song_id)
        analysis_path = song_dir / "analysis.json"
        if not analysis_path.exists():
            raise HTTPException(status_code=404, detail="Song not found")
        analysis = SongAnalysis.model_validate_json(analysis_path.read_text(encoding="utf-8"))

        target: Path | None = None
        if stem == "drums" and analysis.stems.drums:
            target = Path(analysis.stems.drums)
        elif stem == "vocals" and analysis.stems.vocals:
            target = Path(analysis.stems.vocals)
        elif stem == "instruments" and analysis.stems.instruments:
            target = Path(analysis.stems.instruments)
        else:
            target = song_dir / analysis.audio_path

        if not target or not target.exists():
            target = song_dir / analysis.audio_path
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"Audio file missing: {target}")
        return FileResponse(str(target))

    return app