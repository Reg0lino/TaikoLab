"""TaikoLab command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from taikolab.arranger.engine import arrange_song
from taikolab.config import load_config
from taikolab.exporters.diagnostics import format_chart_report
from taikolab.exporters.tja_writer import export_to_opentaiko, write_tja_file
from taikolab.schemas import SongAnalysis, SongOverrides


def _cmd_analyze(args: argparse.Namespace) -> None:
    from taikolab.ingest.pipeline import analyze_song_dir

    cfg = load_config(args.preset)
    song_dir = cfg.song_dir(args.song)
    analyze_song_dir(
        song_id=args.song,
        song_dir=song_dir,
        title=args.title,
        artist=args.artist,
        device=args.device,
    )


def _cmd_import_tab(args: argparse.Namespace) -> None:
    from taikolab.ingest.tab_sync import import_midi_aligned

    cfg = load_config(args.preset)
    song_dir = cfg.song_dir(args.song)
    import_midi_aligned(
        song_id=args.song,
        song_dir=song_dir,
        midi_path=Path(args.tab),
        title=args.title,
        artist=args.artist,
        manual_offset_sec=args.offset,
        use_stems=bool(getattr(args, "use_stems", False)),
    )
    _cmd_arrange(args)


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _cmd_arrange(args: argparse.Namespace) -> None:
    _configure_utf8_stdio()
    cfg = load_config(args.preset)
    song_dir = cfg.song_dir(args.song)
    analysis_path = song_dir / "analysis.json"
    if not analysis_path.exists():
        raise FileNotFoundError(
            f"Missing {analysis_path}. Run `analyze --song {args.song}` or `import-tab` first!"
        )

    analysis = SongAnalysis.model_validate_json(analysis_path.read_text(encoding="utf-8"))
    if not analysis.energy_curve:
        from taikolab.ingest.vocal_riff_tracker import ensure_energy_curve

        if ensure_energy_curve(analysis, song_dir=song_dir):
            analysis_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    overrides: SongOverrides | None = None
    overrides_path = song_dir / "overrides.json"
    if overrides_path.exists():
        overrides = SongOverrides.model_validate_json(overrides_path.read_text(encoding="utf-8"))
        if overrides.preset and not args.preset:
            cfg = load_config(overrides.preset)
        if overrides.target_highway_speed is not None:
            cfg.settings.target_highway_speed = overrides.target_highway_speed
        if overrides.big_note_threshold is not None:
            cfg.preset.big_note_threshold = overrides.big_note_threshold
        if overrides.quiet_guitar_rms_threshold is not None:
            cfg.settings.quiet_guitar_rms_threshold = overrides.quiet_guitar_rms_threshold

    chart = arrange_song(analysis, cfg, overrides=overrides)
    tja_path = song_dir / f"{args.song}.tja"
    write_tja_file(
        chart,
        tja_path,
        bpm_deadzone=cfg.settings.smooth_bpm_deadzone,
        normalize_scroll=cfg.settings.normalize_scroll_on_bpm_change,
        target_highway_speed=cfg.settings.target_highway_speed,
    )
    print(format_chart_report(chart))
    print(f"\n[TaikoLab] Wrote chart -> {tja_path}")

    audio_file = song_dir / analysis.audio_path
    copied = export_to_opentaiko(tja_path, audio_file, cfg.settings.opentaiko_songs_dir, folder_name=args.song)
    if copied:
        print(f"[TaikoLab] Exported to Simulator -> {copied}")


def _cmd_build(args: argparse.Namespace) -> None:
    cfg = load_config(args.preset)
    song_dir = cfg.song_dir(args.song)
    analysis_path = song_dir / "analysis.json"
    if args.force or not analysis_path.exists():
        _cmd_analyze(args)
    _cmd_arrange(args)


def _cmd_serve(args: argparse.Namespace) -> None:
    import threading
    import webbrowser
    import uvicorn
    from taikolab.ui.server import create_app

    url = f"http://{args.host}:{args.port}"
    print(f"[TaikoLab] Launching Director's Console at {url}")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


def main() -> None:
    _configure_utf8_stdio()
    parser = argparse.ArgumentParser(prog="taikolab", description="TaikoLab Chart Generator")
    sub = parser.add_subparsers(dest="command", required=True)

    p_an = sub.add_parser("analyze", help="Analyze song stems/audio into cached analysis.json")
    p_an.add_argument("--song", required=True, help="Folder name inside workspace/")
    p_an.add_argument("--title", default=None)
    p_an.add_argument("--artist", default="Unknown")
    p_an.add_argument("--preset", default=None)
    p_an.add_argument("--device", default="cuda")
    p_an.set_defaults(func=_cmd_analyze)

    p_tab = sub.add_parser("import-tab", help="Align MIDI tab to song audio & build .tja")
    p_tab.add_argument("--song", required=True)
    p_tab.add_argument("--tab", required=True, help="Path to .mid / .midi file")
    p_tab.add_argument("--title", default=None)
    p_tab.add_argument("--artist", default="Unknown")
    p_tab.add_argument("--preset", default=None)
    p_tab.add_argument("--offset", type=float, default=None, help="Optional manual lag override in seconds")
    p_tab.add_argument("--use-stems", action="store_true", help="Run Demucs stem separation before MIDI alignment")
    p_tab.set_defaults(func=_cmd_import_tab)

    p_ar = sub.add_parser("arrange", help="Generate .tja quickly from cached analysis.json")
    p_ar.add_argument("--song", required=True, help="Folder name inside workspace/")
    p_ar.add_argument("--preset", default=None)
    p_ar.set_defaults(func=_cmd_arrange)

    p_bu = sub.add_parser("build", help="Analyze (if uncached) and generate .tja")
    p_bu.add_argument("--song", required=True)
    p_bu.add_argument("--title", default=None)
    p_bu.add_argument("--artist", default="Unknown")
    p_bu.add_argument("--preset", default=None)
    p_bu.add_argument("--device", default="cuda")
    p_bu.add_argument("--force", action="store_true")
    p_bu.set_defaults(func=_cmd_build)

    p_ui = sub.add_parser("serve", help="Launch Director's Console Web UI")
    p_ui.add_argument("--host", default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=8765)
    p_ui.add_argument("--no-browser", action="store_true")
    p_ui.set_defaults(func=_cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()