"""FastAPI backend for TaikoLab Director's Console UI."""

from __future__ import annotations

import re
import statistics
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from taikolab.arranger.engine import arrange_song
from taikolab.config import apply_chart_controls_to_config, load_config, load_wizard_preferences, save_wizard_preferences
from taikolab.exporters.diagnostics import analyze_course
from taikolab.exporters.tja_writer import export_to_opentaiko, write_tja_file
from taikolab.schemas import (
    DownbeatMarker,
    SavedProject,
    SectionOverride,
    SongAnalysis,
    SongOverrides,
    TaikoNote,
)
from taikolab.ui.tja_editor import (
    TjaDocument,
    apply_tja_edits,
    load_tja,
    render_tja_edits,
    save_tja_edits,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


class ArrangeResponse(BaseModel):
    song_id: str
    analysis_summary: dict
    overrides: SongOverrides
    chart: dict
    diagnostics: list[dict]
    reference_hits: list[dict]
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


class OpenTjaRequest(BaseModel):
    path: str


def _imported_chart_payload(
    import_id: str,
    document: TjaDocument,
    overrides: SongOverrides,
    exported_path: str | None = None,
) -> ArrangeResponse:
    chart = apply_tja_edits(document, overrides)
    _validate_manual_rolls(chart)
    duration = max(
        (measure.end_sec for course in chart.courses for measure in course.measures),
        default=0.0,
    )
    return ArrangeResponse(
        song_id=import_id,
        analysis_summary={
            "title": chart.title,
            "artist": chart.subtitle,
            "duration_sec": duration,
            "audio_path": str(document.audio_path) if document.audio_path else "",
            "source_tja_path": str(document.path),
            "stems_available": ["mix"] if document.audio_path else [],
            "drum_hits": 0,
            "vocal_events": 0,
            "guitar_events": 0,
            "simulator_dir": "",
        },
        overrides=overrides,
        chart=chart.model_dump(),
        diagnostics=[vars(analyze_course(course)) for course in chart.courses],
        reference_hits=[],
        exported_path=exported_path,
    )


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


def auto_split_sections_from_analysis(analysis: SongAnalysis) -> list[SectionOverride]:
    """Segment song into bar-aligned musical sections using GP markers or audio/MIDI structure."""
    from taikolab.arranger.quantizer import build_measure_slots
    from taikolab.arranger.section_builder import auto_build_sections

    slots = build_measure_slots(analysis)
    return auto_build_sections(analysis, slots) or _default_sections_from_analysis(analysis)

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


def _validate_manual_rolls(chart) -> None:
    for course in chart.courses:
        active_roll = False
        balloon_total = 0
        for measure in course.measures:
            for note in measure.notes:
                if note in (
                    TaikoNote.DRUMROLL.value,
                    TaikoNote.BIG_DRUMROLL.value,
                    TaikoNote.BALLOON.value,
                ):
                    if active_roll:
                        raise HTTPException(
                            status_code=422,
                            detail=f"{course.difficulty.title()} has overlapping roll markers",
                        )
                    active_roll = True
                    balloon_total += note == TaikoNote.BALLOON.value
                elif note == TaikoNote.END.value:
                    if not active_roll:
                        raise HTTPException(
                            status_code=422,
                            detail=f"{course.difficulty.title()} has an end marker without a roll",
                        )
                    active_roll = False

        if active_roll:
            raise HTTPException(
                status_code=422,
                detail=f"{course.difficulty.title()} has an unclosed roll or balloon",
            )
        if balloon_total != len(course.balloon_counts):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{course.difficulty.title()} has {balloon_total} balloon(s) but "
                    f"{len(course.balloon_counts)} balloon count(s)"
                ),
            )


def _build_chart_payload(song_id: str, overrides: SongOverrides | None = None, export_sim: bool = False) -> ArrangeResponse:
    cfg = load_config(overrides.preset if overrides else None)
    song_dir = cfg.song_dir(song_id)
    analysis_path = song_dir / "analysis.json"
    if not analysis_path.exists():
        raise HTTPException(status_code=404, detail=f"Missing analysis.json for '{song_id}'")

    analysis = SongAnalysis.model_validate_json(analysis_path.read_text(encoding="utf-8"))
    if not analysis.energy_curve:
        from taikolab.ingest.vocal_riff_tracker import ensure_energy_curve

        if ensure_energy_curve(analysis, song_dir=song_dir):
            analysis_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    overrides_path = song_dir / "overrides.json"
    if overrides is None:
        if overrides_path.exists():
            overrides = SongOverrides.model_validate_json(overrides_path.read_text(encoding="utf-8"))
            if not overrides.sections:
                overrides.sections = _default_sections_from_analysis(analysis)
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
    apply_chart_controls_to_config(cfg, overrides)
    chart = arrange_song(analysis, cfg, overrides=overrides)
    for course in chart.courses:
        if course.difficulty in overrides.balloon_counts_by_course:
            course.balloon_counts = overrides.balloon_counts_by_course[course.difficulty]
    _validate_manual_rolls(chart)
    tja_path = song_dir / f"{song_id}.tja"
    write_tja_file(
        chart,
        tja_path,
        bpm_deadzone=cfg.settings.smooth_bpm_deadzone,
        normalize_scroll=cfg.settings.normalize_scroll_on_bpm_change,
        target_highway_speed=cfg.settings.target_highway_speed,
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
        reference_hits=[
            {
                "time_sec": hit.time_sec,
                "drum_class": hit.drum_class,
                "velocity": hit.velocity,
            }
            for hit in analysis.drum_hits
            if hit.source == "tab"
        ],
        exported_path=exported_str,
    )


def create_app() -> FastAPI:
    app = FastAPI(title="TaikoLab Director's Console")
    imported_tja: dict[str, TjaDocument] = {}

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.post("/api/import-tja")
    def import_tja(req: OpenTjaRequest):
        try:
            document = load_tja(Path(req.path))
        except (OSError, UnicodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        import_id = uuid.uuid4().hex
        imported_tja[import_id] = document
        return {"import_id": import_id, "title": document.chart.title}

    @app.get("/api/imported/{import_id}")
    def get_imported_tja(import_id: str):
        document = imported_tja.get(import_id)
        if document is None:
            raise HTTPException(status_code=404, detail="This imported chart session has expired; open the TJA again.")
        overrides = SongOverrides(
            song_id=import_id,
            title=document.chart.title,
            artist=document.chart.subtitle,
            balloon_counts_by_course={
                course.difficulty: course.balloon_counts
                for course in document.chart.courses
                if course.balloon_counts
            },
        )
        return _imported_chart_payload(import_id, document, overrides)

    @app.post("/api/imported/{import_id}/arrange")
    def arrange_imported_tja(import_id: str, overrides: SongOverrides):
        document = imported_tja.get(import_id)
        if document is None:
            raise HTTPException(status_code=404, detail="This imported chart session has expired; open the TJA again.")
        if overrides.song_id != import_id:
            raise HTTPException(status_code=422, detail="The edits do not match the opened TJA chart.")
        try:
            return _imported_chart_payload(import_id, document, overrides)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/imported/{import_id}/export")
    def save_imported_tja(import_id: str, overrides: SongOverrides):
        document = imported_tja.get(import_id)
        if document is None:
            raise HTTPException(status_code=404, detail="This imported chart session has expired; open the TJA again.")
        if overrides.song_id != import_id:
            raise HTTPException(status_code=422, detail="The edits do not match the opened TJA chart.")
        try:
            chart = apply_tja_edits(document, overrides)
            _validate_manual_rolls(chart)
            path = save_tja_edits(document, chart, overrides=overrides)
            document = load_tja(path)
            imported_tja[import_id] = document
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        saved_overrides = SongOverrides(
            song_id=import_id,
            title=document.chart.title,
            artist=document.chart.subtitle,
            global_offset_ms=0.0,
            barline_times=[],
            course_sources={},
            disabled_courses=[],
            balloon_counts_by_course={
                course.difficulty: course.balloon_counts
                for course in document.chart.courses
                if course.balloon_counts
            },
        )
        return _imported_chart_payload(import_id, document, saved_overrides, str(path))

    @app.get("/api/imported/{import_id}/audio")
    def stream_imported_audio(import_id: str):
        document = imported_tja.get(import_id)
        if document is None:
            raise HTTPException(status_code=404, detail="This imported chart session has expired; open the TJA again.")
        if document.audio_path is None:
            raise HTTPException(status_code=404, detail="The TJA WAVE audio file was not found beside the chart.")
        return FileResponse(str(document.audio_path))

    def _saved_projects_dir() -> Path:
        cfg = load_config()
        ws = Path(cfg.settings.workspace_dir)
        if not ws.is_absolute():
            ws = cfg.root_dir / ws
        proj_dir = ws / "saved_projects"
        proj_dir.mkdir(parents=True, exist_ok=True)
        return proj_dir

    @app.get("/api/projects")
    def list_projects():
        proj_dir = _saved_projects_dir()
        items = []
        for path in sorted(proj_dir.glob("*.taikolab.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                proj = SavedProject.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            items.append({
                "project_id": path.name.removesuffix(".taikolab.json"),
                "project_name": proj.project_name,
                "mode": proj.mode,
                "song_id": proj.song_id,
                "source_tja_path": proj.source_tja_path,
                "active_difficulty": proj.active_difficulty,
                "saved_at": proj.saved_at,
            })
        return {"projects": items}

    @app.post("/api/projects/save")
    def save_project(project: SavedProject):
        proj_dir = _saved_projects_dir()
        slug = re.sub(r"[^a-z0-9._-]+", "-", project.project_name.strip().lower()).strip("-") or "project"
        saved = project.model_copy(
            update={"saved_at": datetime.now().isoformat(timespec="seconds")}
        )
        proj_path = proj_dir / f"{slug}.taikolab.json"
        draft_path = proj_dir / f"{slug}.draft.tja"

        try:
            if saved.mode == "imported_tja":
                if not saved.source_tja_path:
                    raise ValueError("Missing source_tja_path for imported TJA project.")
                doc = load_tja(Path(saved.source_tja_path))
                draft_chart = apply_tja_edits(doc, saved.overrides)
                _validate_manual_rolls(draft_chart)
                draft_path.write_bytes(render_tja_edits(doc, draft_chart, overrides=saved.overrides))
            elif saved.song_id:
                cfg = load_config(saved.overrides.preset)
                analysis_path = cfg.song_dir(saved.song_id) / "analysis.json"
                if analysis_path.exists():
                    analysis = SongAnalysis.model_validate_json(analysis_path.read_text(encoding="utf-8"))
                    apply_chart_controls_to_config(cfg, saved.overrides)
                    draft_chart = arrange_song(analysis, cfg, overrides=saved.overrides)
                    write_tja_file(
                        draft_chart,
                        draft_path,
                        bpm_deadzone=cfg.settings.smooth_bpm_deadzone,
                        normalize_scroll=cfg.settings.normalize_scroll_on_bpm_change,
                        target_highway_speed=cfg.settings.target_highway_speed,
                    )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        proj_path.write_text(saved.model_dump_json(indent=2), encoding="utf-8")
        return {
            "project_id": slug,
            "project_name": saved.project_name,
            "path": str(proj_path),
            "draft_tja_path": str(draft_path) if draft_path.exists() else None,
            "saved_at": saved.saved_at,
        }

    @app.post("/api/projects/{project_id}/open")
    def open_project(project_id: str):
        safe_id = Path(project_id).name
        proj_path = _saved_projects_dir() / f"{safe_id}.taikolab.json"
        if not proj_path.is_file():
            raise HTTPException(status_code=404, detail=f"Saved project '{project_id}' not found.")
        try:
            proj = SavedProject.model_validate_json(proj_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Corrupt project file: {exc}") from exc

        if proj.mode == "imported_tja":
            if not proj.source_tja_path:
                raise HTTPException(status_code=422, detail="Saved project is missing source_tja_path.")
            try:
                document = load_tja(Path(proj.source_tja_path))
            except (OSError, UnicodeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            import_id = uuid.uuid4().hex
            imported_tja[import_id] = document
            updated_ov = proj.overrides.model_copy(update={"song_id": import_id})
            return {"project": proj.model_copy(update={"overrides": updated_ov}).model_dump(), "import_id": import_id}

        return {"project": proj.model_dump(), "import_id": None}

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str):
        safe_id = Path(project_id).name
        proj_dir = _saved_projects_dir()
        proj_path = proj_dir / f"{safe_id}.taikolab.json"
        draft_path = proj_dir / f"{safe_id}.draft.tja"
        if not proj_path.is_file():
            raise HTTPException(status_code=404, detail="Saved project not found.")
        proj_path.unlink(missing_ok=True)
        draft_path.unlink(missing_ok=True)
        return {"deleted": safe_id}

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

    @app.post("/api/songs/{song_id}/sections/auto-split")
    def auto_split_sections_api(song_id: str):
        cfg = load_config()
        analysis_path = cfg.song_dir(song_id) / "analysis.json"
        if not analysis_path.exists():
            raise HTTPException(status_code=404, detail="Song not found")
        analysis = SongAnalysis.model_validate_json(analysis_path.read_text(encoding="utf-8"))
        sections = auto_split_sections_from_analysis(analysis)
        return {"sections": [s.model_dump() for s in sections]}

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

    @app.get("/api/slider-profiles")
    def list_slider_profiles():
        cfg = load_config()
        prefs = load_wizard_preferences(cfg.root_dir)
        profiles = prefs.get("slider_profiles") or {}
        return {"profiles": profiles if isinstance(profiles, dict) else {}}

    @app.post("/api/slider-profiles/{name}")
    def save_slider_profile(name: str, controls: dict):
        clean_name = name.strip()[:60]
        if not clean_name:
            raise HTTPException(status_code=422, detail="Profile name is required.")
        cfg = load_config()
        prefs = load_wizard_preferences(cfg.root_dir)
        profiles = dict(prefs.get("slider_profiles") or {})
        profiles[clean_name] = controls
        save_wizard_preferences(cfg.root_dir, slider_profiles=profiles)
        return {"name": clean_name, "profiles": profiles}

    @app.delete("/api/slider-profiles/{name}")
    def delete_slider_profile(name: str):
        cfg = load_config()
        prefs = load_wizard_preferences(cfg.root_dir)
        profiles = dict(prefs.get("slider_profiles") or {})
        profiles.pop(name, None)
        save_wizard_preferences(cfg.root_dir, slider_profiles=profiles)
        return {"profiles": profiles}

    return app