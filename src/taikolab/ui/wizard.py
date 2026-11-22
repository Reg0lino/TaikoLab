"""Dark local desktop wizard for creating automatic Taiko charts."""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from urllib.parse import quote

from taikolab.config import load_config, load_wizard_preferences, save_wizard_preferences
from taikolab.metadata import (
    MetadataLookupError,
    TrackSuggestion,
    get_saved_audiometa_api_key,
    read_audio_tags,
    save_audiometa_api_key,
    search_audiometa_tracks,
    suggest_title_artist_from_filename,
)
from taikolab.schemas import ChartControlSettings, DifficultyRuleOverride, SongOverrides
from taikolab.song_setup import (
    make_song_id,
    normalize_songs_directory,
    stage_song_inputs,
    write_build_manifest,
)
from taikolab.ui.embedded_server import EmbeddedEditorServer

_BACKGROUND = "#11161c"
_SURFACE = "#1a222c"
_FIELD = "#0e1319"
_FOREGROUND = "#e7edf5"
_MUTED = "#a3b0bf"
_ACCENT = "#54a6ff"
_DEFAULT_PRESET = "punk_gallop"
_DEFAULT_DENSITY_PERCENT = 100.0
_DEFAULT_PHRASE_VARIATION = {"hard": 0.0, "oni": 0.0}
_DEFAULT_FILL_ROLLS_BY_COURSE = {"easy": 1, "normal": 4, "hard": 5, "oni": 6}
_DEFAULT_GROOVE_TRIPLE_DENSITY_BY_COURSE = {"easy": 0.0, "normal": 15.0, "hard": 45.0, "oni": 70.0}
_DEFAULT_GROOVE_TRIPLE_COMPLEXITY_BY_COURSE = {"normal": 15.0, "hard": 25.0, "oni": 80.0}
_DEFAULT_GROOVE_TRIPLE_RAMP = 50.0
_DEFAULT_GROOVE_TRIPLE_MAX_BPM = 235.0
DEV_CHART_VERSION = "v0.2"


def _style_dark(root: tk.Tk) -> None:
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    style.configure(".", background=_BACKGROUND, foreground=_FOREGROUND, font=("Segoe UI", 10))
    style.configure("TFrame", background=_BACKGROUND)
    style.configure("Surface.TFrame", background=_SURFACE)
    style.configure("TLabel", background=_BACKGROUND, foreground=_FOREGROUND)
    style.configure("Muted.TLabel", background=_BACKGROUND, foreground=_MUTED)
    style.configure("Title.TLabel", font=("Segoe UI Semibold", 18), foreground=_FOREGROUND)
    style.configure("Section.TLabel", font=("Segoe UI Semibold", 11), foreground=_ACCENT)
    style.configure(
        "TCheckbutton",
        background=_SURFACE,
        foreground=_FOREGROUND,
        indicatorcolor=_FIELD,
    )
    style.map(
        "TCheckbutton",
        background=[("active", _SURFACE)],
        foreground=[("disabled", _MUTED)],
        indicatorcolor=[("selected", _ACCENT)],
    )
    style.configure(
        "TButton",
        background=_SURFACE,
        foreground=_FOREGROUND,
        bordercolor="#354353",
        lightcolor=_SURFACE,
        darkcolor=_SURFACE,
        padding=(10, 6),
    )
    style.map(
        "TButton",
        background=[("active", "#273546"), ("disabled", "#202933")],
        foreground=[("disabled", _MUTED)],
    )
    style.configure("Accent.TButton", background="#1c5f9e", foreground="#ffffff")
    style.map("Accent.TButton", background=[("active", "#2878bf")])
    style.configure(
        "TEntry",
        fieldbackground=_FIELD,
        foreground=_FOREGROUND,
        insertcolor=_FOREGROUND,
        bordercolor="#354353",
        lightcolor="#354353",
        darkcolor="#354353",
        padding=6,
    )
    style.configure(
        "TCombobox",
        fieldbackground=_FIELD,
        background=_SURFACE,
        foreground=_FOREGROUND,
        arrowcolor=_FOREGROUND,
        bordercolor="#354353",
        lightcolor="#354353",
        darkcolor="#354353",
        padding=5,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", _FIELD), ("disabled", _SURFACE)],
        foreground=[("readonly", _FOREGROUND), ("disabled", _MUTED)],
        selectbackground=[("readonly", _FIELD)],
        selectforeground=[("readonly", _FOREGROUND)],
    )
    style.configure(
        "TScrollbar",
        background=_SURFACE,
        troughcolor=_FIELD,
        bordercolor=_BACKGROUND,
        arrowcolor=_FOREGROUND,
    )
    style.configure(
        "Horizontal.TProgressbar",
        background=_ACCENT,
        troughcolor=_FIELD,
        bordercolor=_BACKGROUND,
    )
    root.option_add("*TCombobox*Listbox.background", _FIELD)
    root.option_add("*TCombobox*Listbox.foreground", _FOREGROUND)
    root.option_add("*TCombobox*Listbox.selectBackground", "#285b87")
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.relief", "flat")


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event: tk.Event) -> None:
        if self.window is not None:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        ttk.Label(
            self.window,
            text=self.text,
            justify="left",
            wraplength=420,
            padding=8,
            style="Muted.TLabel",
        ).pack()

    def _hide(self, _event: tk.Event) -> None:
        if self.window is not None:
            self.window.destroy()
            self.window = None


class TaikoWizard:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = load_config(_DEFAULT_PRESET)
        preferences = load_wizard_preferences(self.cfg.root_dir)
        chart_controls = ChartControlSettings.model_validate(
            preferences.get("chart_controls", {})
        )
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.audio_path = tk.StringVar()
        self.midi_path = tk.StringVar()
        self.last_audio_directory = self._saved_picker_directory(
            preferences.get("last_audio_directory")
        )
        self.last_midi_directory = self._saved_picker_directory(
            preferences.get("last_midi_directory"),
            fallback=self.last_audio_directory,
        )
        self.title = tk.StringVar()
        self.artist = tk.StringVar()
        self.songs_directory = tk.StringVar(value=self.cfg.settings.opentaiko_songs_dir)
        self.highway_speed = tk.DoubleVar(
            value=chart_controls.target_highway_speed
            if chart_controls.target_highway_speed is not None
            else self.cfg.settings.target_highway_speed
        )
        self.big_note_threshold = tk.DoubleVar(
            value=chart_controls.big_note_threshold
            if chart_controls.big_note_threshold is not None
            else self.cfg.preset.big_note_threshold
        )
        self.quiet_guitar_threshold = tk.DoubleVar(
            value=chart_controls.quiet_guitar_rms_threshold
            if chart_controls.quiet_guitar_rms_threshold is not None
            else self.cfg.settings.quiet_guitar_rms_threshold
        )
        self.quiet_guitar_behavior = tk.StringVar(
            value={
                "downbeat": "One downbeat",
                "empty": "Leave empty",
            }.get(chart_controls.quiet_guitar_behavior or "downbeat", "One downbeat")
        )
        self.fill_roll_caps = {
            difficulty: tk.DoubleVar(
                value=chart_controls.fill_rolls_by_course.get(
                    difficulty,
                    (
                        chart_controls.fill_rolls_per_course
                        if chart_controls.fill_rolls_per_course is not None
                        else _DEFAULT_FILL_ROLLS_BY_COURSE[difficulty]
                    ),
                )
            )
            for difficulty in ("easy", "normal", "hard", "oni")
        }
        self.groove_triple_densities = {
            difficulty: tk.DoubleVar(
                value=(
                    chart_controls.groove_triple_density_by_course[difficulty] * 100.0
                    if difficulty in chart_controls.groove_triple_density_by_course
                    else _DEFAULT_GROOVE_TRIPLE_DENSITY_BY_COURSE[difficulty]
                )
            )
            for difficulty in ("easy", "normal", "hard", "oni")
        }
        self.groove_triple_complexities = {
            difficulty: tk.DoubleVar(
                value=(
                    chart_controls.groove_triple_complexity_by_course[difficulty] * 100.0
                    if difficulty in chart_controls.groove_triple_complexity_by_course
                    else (
                        chart_controls.groove_triple_complexity * 100.0
                        if chart_controls.groove_triple_complexity is not None
                        else _DEFAULT_GROOVE_TRIPLE_COMPLEXITY_BY_COURSE[difficulty]
                    )
                )
            )
            for difficulty in ("normal", "hard", "oni")
        }
        self.groove_triple_ramp = tk.DoubleVar(
            value=(
                chart_controls.groove_triple_ramp * 100.0
                if chart_controls.groove_triple_ramp is not None
                else _DEFAULT_GROOVE_TRIPLE_RAMP
            )
        )
        self.groove_triple_max_bpm = tk.DoubleVar(
            value=(
                chart_controls.groove_triple_max_bpm
                if chart_controls.groove_triple_max_bpm is not None
                else _DEFAULT_GROOVE_TRIPLE_MAX_BPM
            )
        )
        self.density_scales = {
            difficulty: tk.DoubleVar(
                value=(
                    chart_controls.difficulty_overrides[difficulty].density_scale * 100
                    if difficulty in chart_controls.difficulty_overrides
                    and chart_controls.difficulty_overrides[difficulty].density_scale is not None
                    else _DEFAULT_DENSITY_PERCENT
                )
            )
            for difficulty in ("easy", "normal", "hard", "oni")
        }
        self.phrase_variations = {
            difficulty: tk.DoubleVar(
                value=(
                    chart_controls.difficulty_overrides[difficulty].phrase_variation * 100
                    if difficulty in chart_controls.difficulty_overrides
                    and chart_controls.difficulty_overrides[difficulty].phrase_variation is not None
                    else _DEFAULT_PHRASE_VARIATION[difficulty]
                )
            )
            for difficulty in ("hard", "oni")
        }
        source_labels = {
            "auto": "Automatic",
            "drums": "Drums",
            "hybrid": "Balanced",
            "vocals": "Vocals",
            "guitar": "Guitar",
        }
        default_source_modes = {
            "easy": self.cfg.preset.normal_follow_mode,
            "normal": self.cfg.preset.normal_follow_mode,
            "hard": self.cfg.preset.hard_follow_mode,
            "oni": self.cfg.preset.oni_follow_mode,
        }

        def source_mode_label(difficulty: str) -> str:
            rule = chart_controls.difficulty_overrides.get(difficulty)
            mode = (
                rule.follow_mode
                if rule and rule.follow_mode is not None
                else default_source_modes[difficulty]
            )
            return source_labels.get(mode, "Automatic")

        self.source_modes = {
            difficulty: tk.StringVar(
                value=source_mode_label(difficulty)
            )
            for difficulty in ("easy", "normal", "hard", "oni")
        }
        self.use_demucs_stems = tk.BooleanVar(
            value=bool(preferences.get("use_demucs_stems", False))
        )
        self.append_version_tag = tk.BooleanVar(
            value=bool(preferences.get("append_version_tag", True))
        )
        self.api_key = tk.StringVar(value=get_saved_audiometa_api_key() or "")
        self.metadata_status = tk.StringVar(value="Audio tags and filename suggestions are local.")
        self.output_preview = tk.StringVar()
        self.suggestions: list[TrackSuggestion] = []
        self.metadata_source = "manual"
        self._build_running = False
        self.last_built_song_id: str | None = None
        self.editor_server = EmbeddedEditorServer()
        self.editor_view: ttk.Frame | None = None
        self.editor_webview: tk.Widget | None = None
        self.editor_title_label: ttk.Label | None = None
        self._com_initialized = False

        root.title("TaikoLab Auto Chart Builder")
        root.geometry("920x1040")
        root.minsize(800, 700)
        root.configure(background=_BACKGROUND)
        _style_dark(root)
        self._build_widgets()
        # Refocus Tk controls because the embedded WebView2 surface owns a native child window.
        root.bind_all("<Button-1>", self._focus_editor_control, add="+")
        root.protocol("WM_DELETE_WINDOW", self._close_app)
        self._update_output_preview()
        self.title.trace_add("write", self._update_output_preview)
        self.artist.trace_add("write", self._update_output_preview)
        self.append_version_tag.trace_add("write", self._update_output_preview)
        root.after(100, self._drain_events)

    def _build_widgets(self) -> None:
        container = ttk.Frame(self.root)
        container.pack(fill="both", expand=True)
        self.main_container = container
        canvas = tk.Canvas(container, background=_BACKGROUND, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        outer = ttk.Frame(canvas, padding=(22, 18))
        canvas_window = canvas.create_window((0, 0), window=outer, anchor="nw")

        def update_scroll_region(event: tk.Event) -> None:
            del event
            canvas.configure(scrollregion=canvas.bbox("all"))

        outer.bind("<Configure>", update_scroll_region)
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(canvas_window, width=event.width),
        )
        canvas.bind_all(
            "<MouseWheel>",
            lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"),
        )
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(8, weight=1)

        ttk.Label(outer, text="TaikoLab Auto Chart Builder", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            outer,
            text="Choose the audio and optional MIDI. The wizard stages the sources, builds four courses, and exports them.",
            style="Muted.TLabel",
            wraplength=850,
        ).grid(row=1, column=0, sticky="w", pady=(5, 14))

        files = ttk.Frame(outer, style="Surface.TFrame", padding=14)
        files.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        files.columnconfigure(1, weight=1)
        ttk.Label(files, text="1  Source files", style="Section.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        ttk.Label(files, text="Audio (required)").grid(row=1, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(files, textvariable=self.audio_path).grid(row=1, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(files, text="Browse…", command=self._browse_audio).grid(row=1, column=2)
        ttk.Label(files, text="MIDI (recommended)").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
        ttk.Entry(files, textvariable=self.midi_path).grid(row=2, column=1, sticky="ew", padx=(0, 8), pady=(8, 0))
        ttk.Button(files, text="Browse…", command=self._browse_midi).grid(row=2, column=2, pady=(8, 0))
        ttk.Label(
            files,
            text="GP files are not used in this version. MIDI is optional; without it, local audio analysis is used.",
            style="Muted.TLabel",
            wraplength=800,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            files,
            text="Separate stems with Demucs (GPU heavy — leave unchecked for fast lightweight MIDI builds)",
            variable=self.use_demucs_stems,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            files,
            text=f"Append dev version tag (--auto{DEV_CHART_VERSION}) to in-game chart title",
            variable=self.append_version_tag,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))

        metadata = ttk.Frame(outer, style="Surface.TFrame", padding=14)
        metadata.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        metadata.columnconfigure(1, weight=1)
        ttk.Label(metadata, text="2  Song details", style="Section.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        ttk.Label(metadata, text="Title").grid(row=1, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(metadata, textvariable=self.title).grid(row=1, column=1, columnspan=2, sticky="ew")
        ttk.Label(metadata, text="Artist").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
        ttk.Entry(metadata, textvariable=self.artist).grid(
            row=2, column=1, sticky="ew", padx=(0, 8), pady=(8, 0)
        )
        self.lookup_button = ttk.Button(metadata, text="Look up Audiometa", command=self._lookup_metadata)
        self.lookup_button.grid(row=2, column=2, pady=(8, 0))
        self.suggestion_box = ttk.Combobox(metadata, state="readonly")
        self.suggestion_box.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        self.suggestion_box.bind("<<ComboboxSelected>>", self._select_suggestion)
        ttk.Label(metadata, textvariable=self.metadata_status, style="Muted.TLabel", wraplength=800).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )
        ttk.Label(
            metadata,
            text="Audiometa receives only the artist/title search text over HTTPS, never the audio or MIDI files.",
            style="Muted.TLabel",
            wraplength=800,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(3, 0))

        key_row = ttk.Frame(metadata)
        key_row.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        key_row.columnconfigure(1, weight=1)
        ttk.Label(key_row, text="Audiometa key").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.api_key_entry = ttk.Entry(key_row, textvariable=self.api_key, show="•")
        self.api_key_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            key_row,
            text="Show",
            variable=self.show_key,
            command=self._toggle_key_visibility,
        ).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(key_row, text="Save securely", command=self._save_api_key).grid(row=0, column=3)

        options = ttk.Frame(outer, style="Surface.TFrame", padding=14)
        options.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        options.columnconfigure(1, weight=1)
        ttk.Label(options, text="3  Chart controls and destination", style="Section.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 8)
        )
        ttk.Label(options, text="Chart controls", style="Muted.TLabel").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(0, 4)
        )
        controls = ttk.Frame(options, style="Surface.TFrame")
        controls.grid(row=2, column=0, columnspan=4, sticky="ew")
        controls.columnconfigure(1, weight=1)
        self._add_slider(
            controls,
            0,
            "Highway speed",
            self.highway_speed,
            70,
            280,
            1,
            "{:.0f}",
            default_value=self.cfg.settings.target_highway_speed,
        )
        self._add_slider(
            controls,
            1,
            "Big-note sensitivity (higher = fewer big notes)",
            self.big_note_threshold,
            0.6,
            0.98,
            0.02,
            "{:.2f}",
            default_value=self.cfg.preset.big_note_threshold,
        )
        self._add_slider(
            controls,
            2,
            "Quiet-guitar threshold",
            self.quiet_guitar_threshold,
            0.1,
            0.8,
            0.05,
            "{:.2f}",
            default_value=self.cfg.settings.quiet_guitar_rms_threshold,
        )
        ttk.Label(
            controls,
            text="Higher threshold simplifies more quiet guitar-only measures",
            style="Muted.TLabel",
        ).grid(row=3, column=1, columnspan=3, sticky="w")
        ttk.Label(controls, text="Maximum density by difficulty", style="Muted.TLabel").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )
        for row, difficulty in enumerate(("easy", "normal", "hard", "oni"), start=5):
            self._add_slider(
                controls,
                row,
                difficulty.title(),
                self.density_scales[difficulty],
                50,
                150,
                5,
                "{:.0f}%",
                default_value=_DEFAULT_DENSITY_PERCENT,
            )
        ttk.Label(controls, text="Source followed by each course", style="Muted.TLabel").grid(
            row=9, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )
        for row, difficulty in enumerate(("easy", "normal", "hard", "oni"), start=10):
            ttk.Label(controls, text=difficulty.title()).grid(
                row=row, column=0, sticky="w", padx=(0, 10)
            )
            ttk.Combobox(
                controls,
                textvariable=self.source_modes[difficulty],
                values=("Automatic", "Drums", "Balanced", "Vocals", "Guitar"),
                state="readonly",
                width=14,
            ).grid(row=row, column=1, sticky="w")
        ttk.Label(
            controls,
            text="Repeat color variation (changes existing Don/Ka hits only)",
            style="Muted.TLabel",
        ).grid(row=14, column=0, columnspan=3, sticky="w", pady=(8, 0))
        for row, difficulty in enumerate(("hard", "oni"), start=15):
            self._add_slider(
                controls,
                row,
                difficulty.title(),
                self.phrase_variations[difficulty],
                0,
                100,
                5,
                "{:.0f}%",
                default_value=_DEFAULT_PHRASE_VARIATION[difficulty],
            )
        ttk.Label(
            controls,
            text="Maximum gameplay fill rolls by course",
            style="Muted.TLabel",
        ).grid(row=17, column=0, columnspan=4, sticky="w", pady=(8, 0))
        for row, difficulty in enumerate(("easy", "normal", "hard", "oni"), start=18):
            self._add_slider(
                controls,
                row,
                difficulty.title(),
                self.fill_roll_caps[difficulty],
                0,
                12,
                1,
                "{:.0f}",
                default_value=_DEFAULT_FILL_ROLLS_BY_COURSE[difficulty],
            )
        ttk.Label(controls, text="Quiet intro treatment").grid(
            row=22, column=0, sticky="w", padx=(0, 10), pady=(4, 0)
        )
        ttk.Combobox(
            controls,
            textvariable=self.quiet_guitar_behavior,
            values=("One downbeat", "Leave empty"),
            state="readonly",
        ).grid(row=22, column=1, sticky="w", pady=(4, 0))
        self._add_slider(
            controls,
            23,
            "Groove triple max BPM",
            self.groove_triple_max_bpm,
            120,
            300,
            5,
            "{:.0f}",
            default_value=_DEFAULT_GROOVE_TRIPLE_MAX_BPM,
        )
        self._add_slider(
            controls,
            24,
            "Groove triple ending ramp",
            self.groove_triple_ramp,
            0,
            100,
            5,
            "{:.0f}%",
            default_value=_DEFAULT_GROOVE_TRIPLE_RAMP,
        )
        ttk.Label(
            controls,
            text="Groove triple complexity by course (same-color vs alternating)",
            style="Muted.TLabel",
        ).grid(row=25, column=0, columnspan=4, sticky="w", pady=(8, 0))
        for row, difficulty in enumerate(("normal", "hard", "oni"), start=26):
            self._add_slider(
                controls,
                row,
                f"{difficulty.title()} complexity",
                self.groove_triple_complexities[difficulty],
                0,
                100,
                5,
                "{:.0f}%",
                default_value=_DEFAULT_GROOVE_TRIPLE_COMPLEXITY_BY_COURSE[difficulty],
            )
        ttk.Label(
            controls,
            text="Groove triple-stroke density (0-100% of sparse repeating beats)",
            style="Muted.TLabel",
        ).grid(row=29, column=0, columnspan=4, sticky="w", pady=(8, 0))
        for row, difficulty in enumerate(("easy", "normal", "hard", "oni"), start=30):
            self._add_slider(
                controls,
                row,
                difficulty.title(),
                self.groove_triple_densities[difficulty],
                0,
                100,
                5,
                "{:.0f}%",
                default_value=_DEFAULT_GROOVE_TRIPLE_DENSITY_BY_COURSE[difficulty],
            )

        ttk.Label(options, text="Output folder").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
        ttk.Entry(options, textvariable=self.songs_directory).grid(
            row=3, column=1, columnspan=2, sticky="ew", padx=(0, 8), pady=(8, 0)
        )
        ttk.Button(options, text="Choose folder…", command=self._browse_songs_folder).grid(
            row=3, column=3, pady=(8, 0)
        )
        ttk.Button(options, text="Choose YataiDON.exe…", command=self._browse_game_executable).grid(
            row=4, column=1, sticky="w", pady=(8, 0)
        )
        ttk.Label(
            options,
            textvariable=self.output_preview,
            style="Muted.TLabel",
            wraplength=800,
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(10, 0))

        ttk.Label(outer, text="4  Build progress", style="Section.TLabel").grid(
            row=5, column=0, sticky="w", pady=(2, 6)
        )
        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.grid(row=6, column=0, sticky="ew", pady=(0, 8))
        self.log = ScrolledText(
            outer,
            height=8,
            wrap="word",
            bg=_FIELD,
            fg=_FOREGROUND,
            insertbackground=_FOREGROUND,
            selectbackground="#285b87",
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=1,
            font=("Consolas", 9),
        )
        self.log.grid(row=8, column=0, sticky="nsew")
        self.log.configure(state="disabled")
        buttons = ttk.Frame(outer)
        buttons.grid(row=9, column=0, sticky="ew", pady=(12, 0))
        buttons.columnconfigure(0, weight=1)
        self.editor_button = ttk.Button(
            buttons,
            text="Open Manual Editor",
            command=self._open_last_editor,
        )
        self.editor_button.grid(row=0, column=1, padx=(0, 8), sticky="e")
        ToolTip(
            self.editor_button,
            "Open the last built chart, or open the editor now and choose an existing .tja file. "
            "No new chart build is required to edit an existing file.",
        )
        self.build_button = ttk.Button(
            buttons,
            text="Build and export chart",
            style="Accent.TButton",
            command=self._start_build,
        )
        self.build_button.grid(row=0, column=2, sticky="e")
        ToolTip(
            self.build_button,
            "Analyze the selected audio and optional MIDI, generate all four courses, "
            "and export the chart to the selected YataiDON/OpenTaiko Songs folder.",
        )

    def _add_slider(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.DoubleVar,
        minimum: float,
        maximum: float,
        resolution: float,
        value_format: str,
        default_value: float,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10))
        scale = tk.Scale(
            parent,
            from_=minimum,
            to=maximum,
            resolution=resolution,
            orient="horizontal",
            variable=variable,
            showvalue=False,
            background=_SURFACE,
            foreground=_FOREGROUND,
            troughcolor=_FIELD,
            activebackground=_ACCENT,
            highlightthickness=0,
            borderwidth=0,
            sliderrelief="flat",
        )
        scale.grid(row=row, column=1, sticky="ew", padx=(0, 8))
        value_label = ttk.Label(parent, style="Muted.TLabel", width=8, anchor="e")
        value_label.grid(row=row, column=2, sticky="e")
        ttk.Button(
            parent,
            text="Reset",
            command=lambda var=variable, value=default_value: var.set(value),
        ).grid(row=row, column=3, sticky="e", padx=(8, 0))

        def update_value(*_args: object) -> None:
            value_label.configure(text=value_format.format(variable.get()))

        variable.trace_add("write", update_value)
        update_value()

    def _browse_audio(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose song audio",
            initialdir=self._picker_initial_directory(
                self.audio_path.get(),
                self.last_audio_directory,
            ),
            filetypes=[
                ("Supported audio", "*.mp3 *.ogg *.wav *.flac"),
                ("All files", "*.*"),
            ],
        )
        if not selected:
            return
        path = Path(selected)
        if path.suffix.lower() not in (".mp3", ".ogg", ".wav", ".flac"):
            messagebox.showerror("Unsupported audio", "Choose an MP3, OGG, WAV, or FLAC audio file.")
            return
        self.audio_path.set(str(path))
        self.last_audio_directory = str(path.parent)
        self._remember_picker_directory(audio_directory=path.parent)
        self._prefill_metadata(path)

    def _prefill_metadata(self, path: Path) -> None:
        self.suggestions = []
        self.suggestion_box.set("")
        self.suggestion_box.configure(values=[])
        tag_error: str | None = None
        try:
            title, artist = read_audio_tags(path)
        except MetadataLookupError as exc:
            title, artist = "", ""
            tag_error = str(exc)
        if title or artist:
            self.metadata_source = "audio tags"
            self.metadata_status.set("Prefilled from local audio tags. Review or edit these fields.")
        else:
            title, artist = suggest_title_artist_from_filename(path)
            self.metadata_source = "filename suggestion" if artist else "manual"
            error_message = f"Could not read audio tags: {tag_error}. " if tag_error else ""
            self.metadata_status.set(
                f"{error_message}Suggested from the filename. Confirm both fields before building."
                if artist
                else f"{error_message}No reliable tags found. Enter the song title and artist."
            )
        self.title.set(title)
        self.artist.set(artist)

    def _browse_midi(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose MIDI tab",
            initialdir=self._picker_initial_directory(
                self.midi_path.get(),
                self.last_midi_directory,
            ),
            filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")],
        )
        if selected:
            self.midi_path.set(selected)
            self.last_midi_directory = str(Path(selected).parent)
            self._remember_picker_directory(midi_directory=Path(selected).parent)

    @staticmethod
    def _saved_picker_directory(value: object, fallback: str | None = None) -> str:
        if isinstance(value, str):
            try:
                directory = Path(value).expanduser()
                if directory.is_dir():
                    return str(directory.resolve())
            except (OSError, ValueError):
                pass
        if fallback:
            try:
                directory = Path(fallback).expanduser()
                if directory.is_dir():
                    return str(directory.resolve())
            except (OSError, ValueError):
                pass
        return str(Path.home())

    @staticmethod
    def _picker_initial_directory(current_path: str, remembered_directory: str) -> str:
        if current_path:
            try:
                path = Path(current_path).expanduser()
                directory = path if path.is_dir() else path.parent
                if directory.is_dir():
                    return str(directory)
            except (OSError, ValueError):
                pass
        try:
            remembered = Path(remembered_directory).expanduser()
            if remembered.is_dir():
                return str(remembered)
        except (OSError, ValueError):
            pass
        return str(Path.home())

    def _remember_picker_directory(
        self,
        *,
        audio_directory: Path | None = None,
        midi_directory: Path | None = None,
    ) -> None:
        try:
            save_wizard_preferences(
                self.cfg.root_dir,
                last_audio_directory=str(audio_directory) if audio_directory else None,
                last_midi_directory=str(midi_directory) if midi_directory else None,
            )
        except (OSError, ValueError) as exc:
            messagebox.showwarning(
                "Folder preference not saved",
                f"The file was selected, but TaikoLab could not remember this folder:\n{exc}",
            )

    def _browse_songs_folder(self) -> None:
        selected = filedialog.askdirectory(title="Choose the YataiDON/OpenTaiko Songs folder")
        if selected:
            self._set_songs_directory(Path(selected))

    def _browse_game_executable(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose YataiDON.exe or OpenTaiko.exe",
            filetypes=[("Windows executable", "*.exe")],
        )
        if selected:
            self._set_songs_directory(Path(selected))

    def _set_songs_directory(self, selected: Path) -> None:
        try:
            songs_dir = normalize_songs_directory(selected)
        except (FileNotFoundError, ValueError) as exc:
            messagebox.showerror("Songs folder not found", str(exc))
            return
        self.songs_directory.set(str(songs_dir))
        try:
            save_wizard_preferences(self.cfg.root_dir, str(songs_dir))
            self.cfg = load_config()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not save setting", str(exc))
            return
        self._update_output_preview()

    def _toggle_key_visibility(self) -> None:
        self.api_key_entry.configure(show="" if self.show_key.get() else "•")

    def _save_api_key(self) -> None:
        key = self.api_key.get().strip()
        if not key:
            messagebox.showerror("Missing key", "Enter your Audiometa API key first.")
            return
        try:
            save_audiometa_api_key(key)
        except MetadataLookupError as exc:
            messagebox.showerror("Could not store API key", str(exc))
            return
        self.metadata_status.set("Audiometa API key saved in the Windows credential store.")
        messagebox.showinfo("API key saved", "The key was saved securely and is not stored in project files.")

    def _lookup_metadata(self) -> None:
        key = self.api_key.get().strip() or get_saved_audiometa_api_key() or ""
        if not key:
            messagebox.showerror("Audiometa key required", "Enter and save your Audiometa API key first.")
            return
        if not self.title.get().strip() and not self.artist.get().strip():
            messagebox.showerror("Search text required", "Choose an audio file or enter a title/artist first.")
            return
        if self._build_running:
            return
        self.lookup_button.configure(state="disabled")
        query_artist = self.artist.get().strip()
        query_title = self.title.get().strip()
        threading.Thread(
            target=self._lookup_worker,
            args=(query_artist, query_title, key),
            daemon=True,
        ).start()

    def _lookup_worker(self, artist: str, title: str, api_key: str) -> None:
        try:
            results = search_audiometa_tracks(artist, title, api_key)
            self.events.put(("metadata", results))
        except MetadataLookupError as exc:
            self.events.put(("metadata_error", str(exc)))

    def _select_suggestion(self, event: tk.Event | None = None) -> None:
        del event
        index = self.suggestion_box.current()
        if 0 <= index < len(self.suggestions):
            selection = self.suggestions[index]
            self.title.set(selection.title)
            self.artist.set(selection.artist)
            self.metadata_source = "Audiometa"
            self.metadata_status.set(
                f"Selected Audiometa result {selection.track_id}. Review the title and artist before building."
            )

    @staticmethod
    def format_chart_title(raw_title: str, append_version: bool) -> str:
        suffix = f"--auto{DEV_CHART_VERSION}" if append_version else "-auto"
        return f"{raw_title.strip()} {suffix}"

    def _update_output_preview(self, *_args: object) -> None:
        suffix = f"--auto{DEV_CHART_VERSION}" if self.append_version_tag.get() else "-auto"
        if not self.title.get().strip() or not self.artist.get().strip():
            self.output_preview.set(f"Folder and TJA will use artist-title-auto; in-game title will end in {suffix}.")
            return
        try:
            song_id = make_song_id(self.artist.get(), self.title.get())
        except ValueError:
            self.output_preview.set("Enter a title and artist containing letters or numbers.")
            return
        chart_title = self.format_chart_title(self.title.get(), self.append_version_tag.get())
        self.output_preview.set(
            f"Workspace: workspace\\{song_id}\\   |   TJA: {song_id}.tja   |   In-game title: {chart_title}"
        )

    def _choose_song_id(self) -> tuple[str, bool, bool] | None:
        base_id = make_song_id(self.artist.get(), self.title.get())
        songs_dir = Path(self.songs_directory.get()).expanduser()
        workspace_dir = self.cfg.song_dir(base_id).parent

        version_slug = DEV_CHART_VERSION.replace(".", "-")
        versioned_id = f"{base_id}-{version_slug}"
        v_idx = 2
        while (workspace_dir / versioned_id).exists() or (songs_dir / versioned_id).exists():
            versioned_id = f"{base_id}-{version_slug}-{v_idx}"
            v_idx += 1

        candidates = [base_id]
        variant = 2
        while (workspace_dir / candidates[-1]).exists() or (songs_dir / candidates[-1]).exists():
            candidates.append(make_song_id(self.artist.get(), self.title.get(), variant=variant))
            variant += 1

        workspace_exists = (workspace_dir / base_id).exists()
        simulator_exists = (songs_dir / base_id).exists()
        if not workspace_exists and not simulator_exists:
            return base_id, False, self.append_version_tag.get()

        dialog = tk.Toplevel(self.root)
        dialog.title("Existing chart options")
        dialog.configure(background=_BACKGROUND)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        result: dict[str, object] = {"choice": None}
        ver_var = tk.BooleanVar(value=self.append_version_tag.get())

        frame = ttk.Frame(dialog, style="Surface.TFrame", padding=18)
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Label(frame, text="Chart Already Exists", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text=f"'{base_id}' already exists in TaikoLab or your YataiDON Songs folder.\nChoose how to save this build:",
            style="TLabel",
            wraplength=460,
        ).pack(anchor="w", pady=(6, 10))
        ttk.Checkbutton(
            frame,
            text=f"Tag chart title with --auto{DEV_CHART_VERSION} (creates separate '{versioned_id}' when keeping previous)",
            variable=ver_var,
        ).pack(anchor="w", pady=(0, 12))

        btn_row = ttk.Frame(frame, style="Surface.TFrame")
        btn_row.pack(fill="x")

        def choose(mode: str | None) -> None:
            result["choice"] = mode
            self.append_version_tag.set(ver_var.get())
            dialog.destroy()

        ttk.Button(
            btn_row,
            text="Keep Previous & Write New Version",
            style="Accent.TButton",
            command=lambda: choose("new"),
        ).pack(fill="x", pady=(0, 6))
        ttk.Button(
            btn_row,
            text="Archive Previous & Replace Existing",
            command=lambda: choose("replace"),
        ).pack(fill="x", pady=(0, 6))
        ttk.Button(
            btn_row,
            text="Cancel",
            command=lambda: choose(None),
        ).pack(fill="x")

        dialog.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        self.root.wait_window(dialog)

        choice = result["choice"]
        if choice is None:
            return None
        if choice == "new":
            new_id = versioned_id if ver_var.get() else candidates[-1]
            return new_id, False, ver_var.get()
        return base_id, True, ver_var.get()

    def _chart_control_settings(self) -> ChartControlSettings:
        source_modes = {
            "Automatic": "auto",
            "Drums": "drums",
            "Balanced": "hybrid",
            "Vocals": "vocals",
            "Guitar": "guitar",
        }
        difficulty_overrides = {
            difficulty: DifficultyRuleOverride(
                follow_mode=source_modes[self.source_modes[difficulty].get()],
                density_scale=float(self.density_scales[difficulty].get()) / 100.0,
                phrase_variation=(
                    float(self.phrase_variations[difficulty].get()) / 100.0
                    if difficulty in self.phrase_variations
                    else 0.0
                ),
            )
            for difficulty in ("easy", "normal", "hard", "oni")
        }
        return ChartControlSettings(
            target_highway_speed=float(self.highway_speed.get()),
            big_note_threshold=float(self.big_note_threshold.get()),
            quiet_guitar_rms_threshold=float(self.quiet_guitar_threshold.get()),
            quiet_guitar_behavior=(
                "downbeat"
                if self.quiet_guitar_behavior.get() == "One downbeat"
                else "empty"
            ),
            fill_rolls_by_course={
                difficulty: round(self.fill_roll_caps[difficulty].get())
                for difficulty in ("easy", "normal", "hard", "oni")
            },
            groove_triple_density_by_course=(
                {
                    difficulty: float(self.groove_triple_densities[difficulty].get()) / 100.0
                    for difficulty in ("easy", "normal", "hard", "oni")
                }
                if hasattr(self, "groove_triple_densities")
                else {}
            ),
            groove_triple_complexity_by_course=(
                {
                    difficulty: float(self.groove_triple_complexities[difficulty].get()) / 100.0
                    for difficulty in ("normal", "hard", "oni")
                }
                if hasattr(self, "groove_triple_complexities")
                else {}
            ),
            groove_triple_ramp=(
                float(self.groove_triple_ramp.get()) / 100.0
                if hasattr(self, "groove_triple_ramp")
                else None
            ),
            groove_triple_max_bpm=(
                float(self.groove_triple_max_bpm.get())
                if hasattr(self, "groove_triple_max_bpm")
                else None
            ),
            difficulty_overrides=difficulty_overrides,
        )

    def _start_build(self) -> None:
        if self._build_running:
            return
        audio = Path(self.audio_path.get().strip()) if self.audio_path.get().strip() else None
        midi_text = self.midi_path.get().strip()
        midi = Path(midi_text) if midi_text else None
        if audio is None or not audio.is_file():
            messagebox.showerror("Audio required", "Choose an existing MP3, OGG, WAV, or FLAC file.")
            return
        if midi is not None and (not midi.is_file() or midi.suffix.lower() not in (".mid", ".midi")):
            messagebox.showerror("MIDI not found", "Choose an existing .mid or .midi file, or clear that field.")
            return
        if not self.title.get().strip() or not self.artist.get().strip():
            messagebox.showerror("Song details required", "Confirm both the song title and artist.")
            return
        if not self.songs_directory.get().strip():
            messagebox.showerror("Songs folder required", "Choose the YataiDON/OpenTaiko Songs folder.")
            return
        try:
            songs_dir = normalize_songs_directory(Path(self.songs_directory.get()))
        except (FileNotFoundError, ValueError) as exc:
            messagebox.showerror("Songs folder not found", str(exc))
            return
        self.songs_directory.set(str(songs_dir))

        try:
            selection = self._choose_song_id()
        except ValueError as exc:
            messagebox.showerror("Invalid song details", str(exc))
            return
        if selection is None:
            return
        song_id, replace_existing, use_version_tag = selection
        title = self.format_chart_title(self.title.get(), use_version_tag)
        artist = self.artist.get().strip()
        preset = _DEFAULT_PRESET
        metadata_source = self.metadata_source
        chart_controls = self._chart_control_settings()
        use_demucs = bool(self.use_demucs_stems.get())
        try:
            save_wizard_preferences(
                self.cfg.root_dir,
                str(songs_dir),
                chart_controls=chart_controls.model_dump(exclude_none=True),
                use_demucs_stems=use_demucs,
                append_version_tag=use_version_tag,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not save setting", str(exc))
            return

        self._build_running = True
        self.build_button.configure(state="disabled")
        self.lookup_button.configure(state="disabled")
        self.progress.start(12)
        self._append_log(f"Starting {artist} - {title} with custom chart settings")
        threading.Thread(
            target=self._build_worker,
            args=(
                song_id,
                audio,
                midi,
                title,
                artist,
                preset,
                metadata_source,
                songs_dir,
                replace_existing,
                chart_controls,
                use_demucs,
            ),
            daemon=True,
        ).start()

    def _build_worker(
        self,
        song_id: str,
        audio_source: Path,
        midi_source: Path | None,
        title: str,
        artist: str,
        preset: str,
        metadata_source: str,
        songs_dir: Path,
        replace_existing: bool,
        chart_controls: ChartControlSettings | None = None,
        use_demucs: bool = False,
    ) -> None:
        song_dir = self.cfg.song_dir(song_id)
        had_workspace = song_dir.exists()
        backup_path: Path | None = None
        failed_path: Path | None = None
        simulator_backups: list[tuple[Path, Path]] = []
        try:
            if replace_existing and song_dir.exists():
                backup_root = song_dir.parent / "_backups"
                backup_root.mkdir(parents=True, exist_ok=True)
                backup_path = backup_root / f"{song_id}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
                shutil.move(str(song_dir), str(backup_path))
                self.events.put(("log", f"Existing workspace folder backed up to {backup_path}"))

            existing_simulator_dir = songs_dir / song_id
            if replace_existing and existing_simulator_dir.is_dir():
                conflicts = [
                    existing_simulator_dir / f"{song_id}.tja",
                    existing_simulator_dir / f"audio{audio_source.suffix.lower()}",
                    existing_simulator_dir / "audio_clean.wav",
                ]
                conflicts = [path for path in conflicts if path.is_file()]
                if conflicts:
                    backup_root = songs_dir.parent / "_TaikoLabBackups" / song_id
                    backup_dir = backup_root / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    for conflict in conflicts:
                        backup = backup_dir / conflict.name
                        shutil.copy2(conflict, backup)
                        simulator_backups.append((conflict, backup))
                    self.events.put(("log", f"Existing simulator files backed up to {backup_dir}"))

            staged_audio, staged_midi = stage_song_inputs(song_dir, audio_source, midi_source)
            if chart_controls is not None:
                overrides = SongOverrides(
                    song_id=song_id,
                    title=title,
                    artist=artist,
                    preset=preset,
                    **chart_controls.model_dump(exclude_none=True),
                )
                (song_dir / "overrides.json").write_text(
                    overrides.model_dump_json(indent=2),
                    encoding="utf-8",
                )
            write_build_manifest(
                song_dir,
                song_id=song_id,
                artist=artist,
                title=title,
                preset=preset,
                audio_source=audio_source,
                midi_source=midi_source,
                metadata_source=metadata_source,
                status="building",
            )

            command = [
                sys.executable,
                "-u",
                "-m",
                "taikolab.cli",
                "import-tab" if staged_midi else "build",
                "--song",
                song_id,
                "--title",
                title,
                "--artist",
                artist,
                "--preset",
                preset,
            ]
            if staged_midi:
                command.extend(("--tab", str(staged_midi)))
                if use_demucs:
                    command.append("--use-stems")
            else:
                command.append("--force")
            self.events.put(("log", f"Staged audio: {staged_audio.name}"))
            self.events.put(("log", f"Staged MIDI: {staged_midi.name}" if staged_midi else "No MIDI selected; using local audio analysis."))
            self.events.put(("log", "Running TaikoLab build. No audio or MIDI is uploaded."))

            env = dict(os.environ)
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            process = subprocess.Popen(
                command,
                cwd=self.cfg.root_dir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            if process.stdout is None:
                raise RuntimeError("Could not open the build process output stream.")
            for line in process.stdout:
                self.events.put(("log", line.rstrip()))
            return_code = process.wait()
            if return_code != 0:
                raise RuntimeError(f"TaikoLab build failed with exit code {return_code}.")

            write_build_manifest(
                song_dir,
                song_id=song_id,
                artist=artist,
                title=title,
                preset=preset,
                audio_source=audio_source,
                midi_source=midi_source,
                metadata_source=metadata_source,
                status="complete",
            )
            self.events.put(("complete", song_dir))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            recovery_errors: list[str] = []
            if backup_path and backup_path.exists():
                try:
                    if song_dir.exists():
                        failed_root = song_dir.parent / "_failed_builds"
                        failed_root.mkdir(parents=True, exist_ok=True)
                        failed_path = failed_root / f"{song_id}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
                        shutil.move(str(song_dir), str(failed_path))
                    shutil.move(str(backup_path), str(song_dir))
                except (OSError, shutil.Error) as recovery_exc:
                    recovery_errors.append(f"Could not restore the previous workspace folder: {recovery_exc}")
            for original, backup in simulator_backups:
                if backup.exists():
                    try:
                        shutil.copy2(backup, original)
                    except OSError as recovery_exc:
                        recovery_errors.append(f"Could not restore {original.name}: {recovery_exc}")
            failure_dir = failed_path if failed_path else (song_dir if not had_workspace else None)
            if failure_dir is not None and failure_dir.exists():
                try:
                    write_build_manifest(
                        failure_dir,
                        song_id=song_id,
                        artist=artist,
                        title=title,
                        preset=preset,
                        audio_source=audio_source,
                        midi_source=midi_source,
                        metadata_source=metadata_source,
                        status="failed",
                        error=str(exc),
                    )
                except OSError as manifest_exc:
                    recovery_errors.append(f"Could not write the failure manifest: {manifest_exc}")
            error_message = str(exc)
            if recovery_errors:
                error_message += "\nRecovery warning(s): " + "; ".join(recovery_errors)
            self.events.put(("build_error", error_message))
            if failed_path and failed_path.exists():
                self.events.put(("log", f"Failed build files preserved at {failed_path}"))

    def _drain_events(self) -> None:
        while True:
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "log":
                self._append_log(str(value))
            elif event == "metadata":
                self.suggestions = value if isinstance(value, list) else []
                self.suggestion_box.configure(values=[item.display_text for item in self.suggestions])
                self.lookup_button.configure(state="normal")
                if self.suggestions:
                    self.suggestion_box.current(0)
                    self._select_suggestion(None)
                    self.metadata_status.set(
                        f"Found {len(self.suggestions)} Audiometa result(s). Confirm the selected match."
                    )
                else:
                    self.metadata_status.set("Audiometa found no track matches. Enter the details manually.")
            elif event == "metadata_error":
                self.lookup_button.configure(state="normal")
                self.metadata_status.set(str(value))
            elif event == "complete":
                self._finish_build()
                song_dir = Path(value)
                self.last_built_song_id = song_dir.name
                self.editor_button.configure(state="normal")
                if messagebox.askyesno(
                    "Chart ready",
                    f"Chart built and exported successfully.\n\nSong folder:\n{song_dir}\n\nOpen the Manual Chart Editor now?",
                ):
                    self._open_last_editor()
            elif event == "build_error":
                self._finish_build()
                messagebox.showerror(
                    "Build failed",
                    f"{value}\n\nSee the build log above. Existing files were restored when possible, "
                    "and partial output was retained separately.",
                )
        self.root.after(100, self._drain_events)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _finish_build(self) -> None:
        self._build_running = False
        self.progress.stop()
        self.build_button.configure(state="normal")
        self.lookup_button.configure(state="normal")

    def _open_last_editor(self) -> None:
        self._show_editor(self.last_built_song_id or "")

    def _show_editor(self, song_id: str) -> None:
        if self.editor_view is not None:
            self.editor_view.lift()
            return

        if sys.platform != "win32":
            messagebox.showerror(
                "Embedded editor unavailable",
                "The embedded editor currently requires Windows and the Microsoft Edge WebView2 Runtime.",
            )
            return

        try:
            from tkwebview import TkWebview
            from tkwebview.core import webview_exception

            base_url = self.editor_server.start()
        except (ImportError, OSError, RuntimeError, TimeoutError) as exc:
            messagebox.showerror("Could not open Manual Editor", str(exc))
            return

        self.main_container.pack_forget()
        self.root.minsize(1100, 700)
        self.root.geometry("1600x950")
        self.editor_view = ttk.Frame(self.root)
        self.editor_view.pack(fill="both", expand=True)
        toolbar = ttk.Frame(self.editor_view, padding=(10, 6))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Back to Builder", command=self._leave_editor).pack(side="left")
        ttk.Button(toolbar, text="Open Existing TJA…", command=self._open_existing_tja).pack(
            side="left", padx=(8, 0)
        )
        self.editor_title_label = ttk.Label(
            toolbar,
            text=f"Manual Chart Editor — {song_id}" if song_id else "Manual Chart Editor",
            style="Section.TLabel",
        )
        self.editor_title_label.pack(side="left", padx=12)
        try:
            self.editor_webview = TkWebview(self.editor_view)
            self.editor_webview.pack(fill="both", expand=True)
            self.editor_webview.navigate(f"{base_url}/?song={quote(song_id)}")
        except webview_exception as exc:
            if self.editor_webview is not None:
                self.editor_webview.destroy_webview()
                self.editor_webview = None
            self.editor_view.destroy()
            self.editor_view = None
            self.editor_title_label = None
            self.main_container.pack(fill="both", expand=True)
            self.root.geometry("920x1040")
            self.root.minsize(800, 700)
            messagebox.showerror(
                "Could not open Manual Editor",
                f"{exc}\n\nConfirm that the Microsoft Edge WebView2 Runtime is installed.",
            )
            return

    def _open_existing_tja(self) -> None:
        selected = filedialog.askopenfilename(
            title="Open an existing Taiko chart",
            filetypes=[("Taiko chart files", "*.tja")],
        )
        if not selected or self.editor_webview is None:
            return
        path = Path(selected)
        try:
            import_id = self.editor_server.open_tja(path)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not open chart", str(exc))
            return
        if self.editor_server.url is None:
            messagebox.showerror("Could not open chart", "The local editor server is not running.")
            return
        if self.editor_title_label is not None:
            self.editor_title_label.configure(text=f"Manual Chart Editor — {path.stem}")
        self.editor_webview.navigate(
            f"{self.editor_server.url}/?import_id={quote(import_id)}"
        )

    def _leave_editor(self) -> None:
        if self.editor_webview is not None:
            self.editor_webview.destroy_webview()
            self.editor_webview = None
        if self.editor_view is not None:
            self.editor_view.destroy()
            self.editor_view = None
            self.editor_title_label = None
        self.root.geometry("920x1040")
        self.root.minsize(800, 700)
        self.main_container.pack(fill="both", expand=True)

    def _focus_editor_control(self, event: tk.Event) -> None:
        if self.editor_view is None:
            return
        try:
            event.widget.focus_force()
        except tk.TclError:
            pass

    def _close_app(self) -> None:
        try:
            if self.editor_webview is not None:
                self.editor_webview.destroy_webview()
                self.editor_webview = None
            self.editor_server.stop()
        except (OSError, RuntimeError) as exc:
            messagebox.showerror("Could not close cleanly", str(exc))
            return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    TaikoWizard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
