# TaikoLab Handover

Updated: 2026-11-20 (Post Editor Sprints 1–5 + UTF-8 CLI Fix)

## Current status and next action

Editor UI Sprints 1–5 and the Windows UTF-8 child-process CLI fix are implemented and passing tests. The immediate next phase is **Pipeline & Arranger Optimization** (Sprints A, B, and C below) to make automatic charting accurate and musical across **80s Pop, New Wave, Pixies, Old Punk, and Skate Punk** (both MIDI-aligned and audio-only), before playtesting final charts.

### Completed in Editor UI Sprints 1–5 + Bugfix

- **UTF-8 child CLI fix (`wizard.py`, `cli.py`):** Forced `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` on child `taikolab.cli` processes and `reconfigure(encoding="utf-8", errors="replace")` in `cli.py` so Unicode hyphens (`\u2010`, e.g. `blink‐182`) and symbols never crash `cp1252` stdout after alignment succeeds.
- **Wizard/Console parity (`server.py`):** Default `sections` are populated when opening wizard-built `overrides.json` files (`sections=[]`), and `target_highway_speed`, `big_note_threshold`, and `quiet_guitar_rms_threshold` from `overrides.json` are applied during UI arrangement/export.
- **Visual toolbox & smoother lane (`index.html`, `styles.css`, `app.js`):** Replaced tool `<select>` with visual palette buttons (`Select [A]`, `Grid [G]`, `Don [D]`, `Ka [K]`, `Big Don [Shift+D]`, `Big Ka [Shift+K]`, `Roll [R]`, `Big Roll [Shift+R]`, `Balloon [B]`, `Erase [E]`), fixed `Select` click so it never mutates/deletes notes, added roll/balloon edge resizing, fixed chronological balloon hit-count insertion and direct editing, added snap-grid ticks on canvas, and stopped WaveSurfer region flicker on note-only edits.
- **Wave zoom, exact click, & scrollbars (`index.html`, `styles.css`, `app.js`):** Normal clicks on `#waveform` seek to the exact spot; `Shift+Click` snaps to section/barline start. Added `Wave Zoom` + `Fit` (with timeline mounted inside WaveSurfer's scroll container) and `Chart Scroll` (`#laneScroll`) + mouse-wheel horizontal scroll / `Ctrl+Wheel` zoom (also works on audio-less imported TJAs).
- **Multi-select, Copy/Paste, & atomic nudging (`app.js`):** Box-drag marquee selection, `Shift+Click` toggle, `◀ -1 Step` / `+1 Step ▶` (`[` / `]` or `Alt+Left/Right`) with exact cross-measure rational stepping, `Flip Don/Ka` (`F`), `Copy`/`Paste` (`Ctrl+C`/`Ctrl+V`), and `Delete` with optimistic canvas updates + coalesced `rebuildChart`.
- **Spacebar lock & Fluid Grid/Barline Warping (`schemas.py`, `quantizer.py`, `difficulty.py`, `engine.py`, `tja_editor.py`, `server.py`, `app.js`):**
  - Capture-phase `Space` lock (`keydown` + `keyup`) strictly toggles Play/Pause without opening dropdowns or activating focused buttons.
  - `Grid [G]` tool allows dragging single barlines or pinning an anchor barline (`[PIN]`) and stretching a phrase across bars (with optional `Shift following` / `Alt` drag) while **locking all notes to their beat slots**. Fixed `SectionOverride.offset_shift_ms` in `quantizer.py` + `difficulty.py` so section shifts move barlines and notes together.
  - Imported `.tja` editing (`tja_editor.py`) now supports fluid `OFFSET`, `#BPMCHANGE`, and proportional `#SCROLL` updates while preserving untouched formatting/courses and `.tja.bak`.
- **Bar-snapped sections, Section Quantize, & Console Chart Sliders (`quantizer.py`, `index.html`, `app.js`):**
  - Added `Split Here` (bar-snapped), `From Selection`, bar-snapped WaveSurfer section edge dragging, `Straighten BPM` (Section Inspector), `Straighten Range` (`Grid` toolbar for both generated and imported TJAs), explicit `Section BPM` (`#secBpm`), and true `0.5x` merge / `2.0x` split in `quantizer.py`.
  - Added collapsible `Chart Sliders` panel in Director's Console for live `Highway Speed`, `Big-Note Sensitivity`, `Quiet-Guitar Threshold/Treatment`, and per-course `Source Mode`, `Density`, `Repeat Color Variation`, and `Fill-Roll Cap`.

### Planned Next Moves: Pipeline & Arranger Upgrade (Not Yet Coded)

**Goal:** Accurate, musical automatic charting across **80s Pop, New Wave, Pixies, Old Punk, and Skate Punk** (both MIDI-aligned and audio-only). Note: re-running deterministic `ADTOF` / `Beat This!` twice does not change output; `.gp` parsing remains deferred because `.mid` already contains drum pitches.

1. **Pipeline Sprint A — Speed Cap + Tom Pitch & Drum Fill Intelligence:**
   - Raise `target_highway_speed` cap from `160` to `280` (`schemas.py`, `wizard.py`, `index.html`).
   - Stop collapsing all MIDI/ADTOF toms (`41–50`) into `"tom": "don"`: preserve `high_tom` vs `low_tom` so fills alternate naturally (`Ka`/`Don`) instead of turning into Don walls (`tab_sync.py`, `drum_transcriber.py`, `mapper_drums.py`, presets).
   - Upgrade `_find_fill_roll_span()` (`hori_filter.py`) to detect multi-drum phrase-end fills for **Rolls** and major energy build-ups for **Balloons** (`7`).
2. **Pipeline Sprint B — Balanced-Favoring-Drums & 2/4-Bar Repeat Variation:**
   - Move past rigid `vocals` vs `drums` silos so drums stay the rhythmic backbone while vocal/guitar hooks fill phrase pockets and reinforce chorus accents (`section_builder.py`, `mapper_fusion.py`).
   - Replace 1-bar exact-match color flipping in `difficulty.py` with **2-bar & 4-bar phrase fingerprinting** + musical repeat variations (trailing `Ka-Ka` turnarounds, ride/cymbal lifts, fill embellishments) without randomly inverting Beat 1 kicks or Beat 2/4 snares.
3. **Pipeline Sprint C — Ingest Accuracy (MIDI+Stems & Audio-Only):**
   - Run/use cached Demucs stems (`stems/drums.wav`, `vocals.wav`, `instruments.wav`) during `import-tab` (`tab_sync.py`) so MIDI alignment snaps to isolated drums instead of noisy full-mix `ADTOF`, restoring `energy_curve` quiet-intro protection and stem audio preview in the editor. Tighten bar-level transient snap instead of over-blurring with a 5-second moving average.
   - Add steady-BPM linear grid locking in `beat_tracker.py` / `quantizer.py` for click-steady 80s Pop / New Wave audio-only songs, plus tom/soft-fill DSP rescue in `drum_transcriber.py` / `drum_dsp.py`.

### First files to inspect

- `src/taikolab/ingest/tab_sync.py` & `drum_transcriber.py` & `beat_tracker.py` — MIDI tom pitch mapping, optional Demucs stem alignment, and steady-BPM grid locking.
- `src/taikolab/arranger/mapper_drums.py`, `mapper_fusion.py`, `hori_filter.py`, `difficulty.py`, `section_builder.py` — fill patterning, balloons, balanced layering, and 2/4-bar phrase variation.
- `src/taikolab/ui/static/index.html`, `app.js`, `styles.css` — Director's Console editor UI.
- `src/taikolab/ui/tja_editor.py`, `server.py`, `wizard.py` — imported-TJA fluid timing/note editing and desktop wizard.
- `run_app.bat` — desktop launcher.
## Automatic charting context (background)

Dumpweed timing was playtested and accepted; its separate variety test was judged good enough for now. The Leaving Song Part II uses the alt-rock profile and shared highway speed; its quiet-guitar intro safeguard was added and the user confirmed the result works well.

The Kill the Poor pickup/export issue is fixed and its chart is ready for playtesting. The user found it satisfying but too repetitive, with a strict kick-to-Don / snare-to-Ka mapping. Holiday in Cambodia's Hard chart is a good baseline but also repeats; Voyeur has the same repetition concern. BYOB was not quite on point, Slipknot's Eyeless did not chart well, and audio-only charting usually fails. Deeper multi-bar repetition detection remains a follow-up.

The user heard phrase-end drift in Foo Fighters' Everlong and over-fast slow outros in Police Truck and All His Suits Are Torn. The timing experiment substantially improved Everlong's MIDI/audio kick-snare alignment and fixed a false fast outro in Police Truck. Validation was run in temporary directories; existing workspace analyses and TJA files were not replaced and still need regeneration/playtesting before promotion.

The wizard has no style selector and defaults to the prior Punk Gallop behavior: Easy/Normal follow vocals, Hard follows drums, and Oni is hybrid. Slider defaults are highway speed 101.343, big-note threshold 0.88, quiet-guitar threshold 0.25, 100% density, zero repeat-color inversion, and fill-roll caps Easy/Normal/Hard/Oni = 3/3/0/0. Simulator backups are outside the selected `Songs` folder. The `taiko_auto` profile remains available but is not the wizard default.

## Kill the Poor export failure and fix

The MIDI/audio import and alignment for Dead Kennedys' Kill the Poor succeeded, but TJA export initially stopped because the Easy course projected 665 ms of grid drift. The cached analysis has a valid one-beat `1/4` pickup from 1.420 to 1.9497 seconds, followed by a `4/4` measure. The quantizer's noise filter rejected that first downbeat interval because it was shorter than 0.65 seconds and contained fewer than two beat events. It merged the pickup with the following `4/4` measure, but retained the pickup's `1/4` meter, producing an impossible first-bar duration and triggering the exporter guard.

`src/taikolab/arranger/quantizer.py` now retains sub-0.65-second spans only when their duration agrees with the active tempo region's meter/BPM estimate. This keeps false short downbeats filtered while preserving legitimate pickup measures; the TJA writer's 100 ms drift guard was not weakened. `test_short_one_beat_pickup_is_not_merged_with_following_measure` covers the regression.

The cached aligned analysis was reused; no second DTW run was needed. `taikolab.cli arrange --song dead-kennedys-kill-the-poor-auto --preset punk_gallop` now renders and exports the chart. All four courses contain 151 measures, include the opening `1/4` pickup, and pass the drift guard. The chart uses `OFFSET:-1.420`; the workspace and simulator TJA/audio files were verified identical. The failed-attempt copy remains preserved under `workspace/_failed_builds/`.

## Open timing note: Descendents - Hope

The user reports that the Hope chart is mostly playable but drifts in the middle. They suspect the original performance may not follow a metronome. No Hope timing changes were made here. Before changing the global offset or accepting a fixed tempo, use loud click overlays at the beginning, middle, and end to compare MIDI hits against the actual drums. A constant error suggests an offset; an error that grows or changes direction suggests a changing tempo map or alignment issue. If the recording itself speeds up or slows down, preserve its local timing and consider custom chart adjustments rather than forcing a rigid beat.

## Automatic song wizard

The current implementation adds a native, dark Tkinter desktop wizard for creating `-auto` charts without Gemini/OpenAI or cloud audio processing. Double-click `run_app.bat` to open the app; it calls `scripts\run_wizard.ps1`. You can also launch it from the repository root with `.\scripts\run_wizard.ps1`, or run `taikolab-wizard` from the installed project environment. `run_build.bat` is only for rebuilding a chart from cached analysis and does not open the app. Install wizard dependencies with `.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"`; the manual editor additionally needs the `ui` extra and Microsoft Edge WebView2 Runtime.

The wizard requires one audio file (MP3, OGG, WAV, or FLAC) and accepts an optional MIDI file. GP is intentionally not an input: TaikoLab does not parse Guitar Pro files. The wizard reads local title/artist tags with Mutagen, then suggests a title/artist from a common `artist - title` filename if tags are absent. The user must confirm or edit both values. It adds `-auto` to the in-game title and creates a filesystem-safe artist/title ID ending in `-auto`; name conflicts offer a separate numbered ID or an explicit replacement with backups.

The Audiometa lookup is optional. It sends only artist/title text over HTTPS to `https://api.audiometa.io/v1/search`; it never uploads audio, MIDI, or GP. The API key is read from `AUDIOMETA_API_KEY` or the OS credential store and is never written to project JSON or the build manifest. Use **Save securely** in the UI to store a key in Windows Credential Manager. If no key/backend or API connection is available, metadata remains editable and the local build flow still works. Do not paste the key into chat or commit it.

The ignored `configs/wizard_settings.json` also remembers the audio picker and MIDI picker directories independently. Each successful selection saves that file's parent folder. A first-time MIDI picker uses the remembered audio folder; after a MIDI folder is selected, it remembers and reuses that folder independently. If a saved directory no longer exists, the picker falls back to the home folder (or the remembered audio folder for MIDI).

Choose either the game's `Songs` folder or its executable in the destination controls. The wizard normalizes an executable/install-folder selection to an existing `Songs` folder and rejects ambiguous or missing destinations. This preference is persisted in ignored `configs/wizard_settings.json` and overlays `opentaiko_songs_dir` at config load time without rewriting the commented YAML. New builds stage standardized `audio.<ext>` and optional `input.mid` files in `workspace/<artist-title-auto>/`, then call the existing `taikolab.cli import-tab` path when MIDI is present or `taikolab.cli build --force` for audio-only input. The existing CLI remains responsible for chart generation and simulator export.

The wizard's build manifest records non-secret source names, metadata origin, preset, and status. Replacements first move the existing TaikoLab workspace song to `workspace/_backups/`; conflicting generated simulator TJA/audio files are copied to `<parent-of-Songs>/_TaikoLabBackups/`, outside the `Songs` tree so the game does not discover backup folders as songs. A failed replacement restores the prior workspace and simulator files and retains new partial files under `_failed_builds`. Existing non-conflicting files in the simulator folder are left alone. Review the confirmation before replacing a chart.

The wizard defaults to the `punk_gallop` profile rather than presenting a genre selector. Per-course source choices map to existing modes: Automatic, Drums, Balanced (hybrid), Vocals, and Guitar. In addition to density and source mode, the UI collects highway speed, big-note sensitivity, quiet-guitar threshold/treatment, Hard/Oni repeat variation, and a fill-roll cap for each course. Each slider resets independently to the legacy baseline: highway speed 101.343, big-note threshold 0.88, quiet-guitar threshold 0.25, density 100%, Hard/Oni repeat variation 0%, and roll caps 3/3/0/0. The Tkinter wizard's saved chart-control preferences were updated to these values. Values are remembered in ignored `configs/wizard_settings.json` and written into each song's `overrides.json`; the CLI applies those overrides during arrangement/export. Highway speed changes scroll only. Density is a cap and cannot add source events. Repeat variation only recolors eligible existing hits, and the current detector recognizes adjacent identical one-measure patterns rather than longer/non-adjacent phrases. Fill rolls are gameplay rolls, not MIDI drumroll transcription; no balloon-roll control is exposed. Source modes are available per course so removing genre selection does not remove its key source-following behavior.

## Manual chart editor: current implementation

The Tkinter wizard's **Open Manual Editor** button is available at launch; it opens the last built chart when available, or opens the editor without a chart so the user can choose an existing TJA. A new build is not required to edit a previous chart. The existing browser UI and Python API are reused; the editor does not replace the automatic build pipeline. Its FastAPI server binds only to `127.0.0.1`, chooses a free port, and is stopped when the editor or wizard closes. Embedding requires Windows, the `tkwebview` package from the `ui` extra, and the Microsoft Edge WebView2 Runtime. Install both wizard and editor dependencies with `.\.venv\Scripts\python.exe -m pip install -e ".[ui,desktop]"`.

The embedded editor is single-instance within the wizard. Earlier repeated open calls created another packed Tk frame each time while the wizard retained references only to the newest frame; returning to the builder could therefore reveal older editor frames underneath it, as in the user's screenshot. `_show_editor` now raises the existing editor frame and returns instead of creating another WebView. Restart the app after updating so any already-stacked frames from an older running version are cleared. A regression test covers reopening an active editor.

The editor toolbar's **Open Existing TJA…** action opens a native file picker and loads that chart directly; it does not require `analysis.json` or rerun audio/MIDI analysis. In imported-chart mode, controls that would only modify generated-chart settings (tap capture, section rebuilding, global offset, and preset selection) are hidden or disabled. The importer supports single-branch Taiko courses (Easy, Normal, Hard, Oni, Edit) with standard note codes 0–8 and preserves the selected file's original metadata, timing commands, and formatting while changing note rows. Unsupported branching charts are rejected with an explicit error. If the TJA `WAVE` file is beside the chart, it is used for preview playback. **Save TJA** updates the selected file and creates `<filename>.tja.bak` before its first save. Imported sessions last for the current app run; select the file again after restarting.

The wide editor keeps WaveSurfer audio/stem playback, seek/loop controls, chart hitsounds, and course selection. Its Taiko lane separates playable notes from a read-only MIDI ghost-hit lane (only analysis events whose source is `tab` are shown). The MIDI-reference toggle and volume are independent of chart clicks. The playable lane supports Don, Ka, Big Don, Big Ka, drag-to-move, right-click/Delete removal, and click-drag creation of drumrolls and balloons. Balloon hit counts are editable per course. Unclosed, overlapping, and unmatched balloon markers are rejected before TJA output.

Snapping is on by default and can be toggled. The grid selector chooses the snapped subdivision; with snapping off, placement uses a 1/64-measure grid and the arranger expands the affected TJA measure to preserve that position. Undo/redo is available through buttons, Ctrl+Z, Ctrl+Y, and Ctrl+Shift+Z. Space toggles playback; Ctrl+S exports; Delete removes the selection; arrows seek; Home/End jump; +/- zoom; Escape cancels a drag; A/D/K/R/B select editing tools; S toggles snap; Numpad 0 records downbeats. The editor includes an on-screen shortcut list and explanatory tooltips.

For a generated chart, edits remain an explicit overlay in `overrides.json`; they do not overwrite the source MIDI or replace its aligned drum-hit events. Export uses the existing arrangement and TJA writer, validates roll/balloon markers, and writes the result to the configured YataiDON/OpenTaiko Songs folder. Imported-TJA mode instead saves directly to the selected source file as described above. Focused API coverage checks MIDI reference serialization, unsnapped 1/64 edits, valid balloon output, and rejection of an unclosed balloon.

The user explicitly deferred the visual toolbox redesign. Keep that out of the current scope; first let them playtest opening and editing existing charts.

Validation completed for this implementation: the full suite passes 67 tests; Ruff and `node --check` pass for the imported-chart changes; the importer parsed existing Everlong, Holiday in Cambodia, and Dumpweed TJA files; an embedded-server smoke test opened Everlong and returned all four courses/660 measures; and tests verify unedited files remain byte-for-byte unchanged, edited TJA saves with a backup, and branch charts are rejected. The Tk wizard's existing-chart picker/navigation, editor singleton, and opening without a build have regression coverage. The WebView widget rendered/resized/closed in a standalone smoke test; browser interaction verified note creation, Ctrl+Z/Ctrl+Y, unsnapped balloon drawing, and successful TJA export. Audio playback and real-song/focus behavior still need a user-side playtest in the installed desktop environment.

## Source organization note

The six historical BYOB, Dumpweed, and Leaving Song MIDI/GP references live under their respective `workspace/<song>/sources/` folders. `incoming_midi/` is not used automatically; it is a user-managed selection folder. The original Hope and Kill the Poor MIDI files remain there. The Kill the Poor MIDI was copied to `workspace/dead-kennedys-kill-the-poor-auto/input.mid` by the wizard and used for its chart. GP files are references only; the working importer uses MIDI, not GP. `scripts/run_tab.ps1` copies its selected MIDI to the song folder as `input.mid`; optional `-Audio` copies an audio source to standardized `audio.<ext>` before import. If either normalized file would be replaced with different content, the old copy is backed up under that song's `sources/` folder.

## Gemini handoff: project map and workflow

This section is intended to give a fresh AI instance (Gemini 4 Hydrogen) enough context to continue without relying on this chat. Read the current-status and manual-editor sections first, then consult this project map. Treat generated chart/audio files as user data: do not overwrite or delete unrelated songs in the large YataiDON library.

```text
TaikoLab/
|-- run_app.bat                     Double-click launcher for the desktop app
|-- run_build.bat                   Double-click launcher for cached-analysis rebuilds
|-- configs/
|   |-- default_settings.yaml       Global offsets, tempo deadzone, highway speed, quiet-guitar threshold
|   |-- wizard_settings.json       Ignored local Songs folder, picker folders, and chart-control defaults
|   |-- hori_default.yaml           Course density, note gaps, roll ergonomics
|   `-- presets/
|       |-- taiko_auto.yaml          Optional general-purpose profile
|       |-- punk_gallop.yaml        Punk chart-source and drum-mapping rules
|       |-- alt_rock.yaml           Added for The Leaving Song Part II
|       |-- mathcore.yaml
|       `-- powerviolence.yaml
|-- incoming_midi/                  Legacy/manual-selection folder with user-staged MIDI inputs
|-- scripts/
|   |-- run_tab.ps1                 MIDI import, audio alignment, arrangement, export
|   |-- run_wizard.ps1              Launch the dark automatic-song wizard
|   |-- run_analyze.ps1
|   |-- run_build.ps1
|   |-- run_tests.ps1
|   `-- check_env.ps1
|-- src/taikolab/
|   |-- arranger/                   MIDI/audio events -> Taiko courses
|   |-- exporters/
|   |   |-- tja_writer.py           TJA timing and highway-scroll serialization
|   |   `-- diagnostics.py
|   |-- ingest/
|   |   |-- stem_loader.py          Full-mix and stem discovery / Demucs
|   |   |-- tab_sync.py             MIDI parsing and audio/stem alignment
|   |   |-- drum_dsp.py             Isolated-stem drum transient detection
|   |   `-- pipeline.py
|   |-- ui/
|   |   |-- wizard.py               Tkinter workflow, editor lifecycle, native TJA picker
|   |   |-- embedded_server.py      Local embedded-editor server client
|   |   |-- server.py               FastAPI build and imported-TJA endpoints
|   |   |-- tja_editor.py           TJA import, edit overlay, preservation, and saving
|   |   `-- static/                 Embedded editor HTML, JavaScript, and CSS
|   |-- cli.py                      CLI arrangement/export path
|   |-- config.py                   Settings and preset models
|   |-- metadata.py                 Local audio tags, Audiometa lookup, secure API-key access
|   |-- song_setup.py               Safe IDs, Songs-folder normalization, input staging, manifest
|   `-- schemas.py
|-- tests/
|   |-- test_exporter_and_arranger.py
|   |-- test_ingest_dsp_and_pipeline.py
|   `-- test_tab_sync.py
|   |-- test_song_wizard.py         Wizard, editor lifecycle, and existing-TJA picker tests
|   `-- test_tja_editor.py          Imported chart parsing/edit/save tests
|-- workspace/
|   |-- byob/sources/               Historical MIDI and GP references
|   |-- dumpweed/sources/           Historical MIDI and GP references
|   `-- leavingsongpart2/
|       |-- sources/                Historical MIDI and GP references
|       |-- theleavingsongpart2.mp3
|       |-- stems/{drums,vocals,bass,instruments}.wav
|       |-- analysis.json
|       |-- audio_clean.wav
|       |-- leavingsongpart2.tja
|       `-- diagnostics/leavingsongpart2_click_reference_0ms.wav
`-- HANDOVER.md

YataiDON-Windows/Songs/
|-- byob/                           TaikoLab export
|-- dumpweed/                       TaikoLab export
|-- leavingsongpart2/               Current playtest export
`-- TaikoLab_Dumpweed_*_Test/       Earlier isolated timing/variety tests
```

The project map above highlights the current editor path. For existing `.tja` work, start with the "Manual chart editor: current implementation" section and its listed files; do not use old pre-editor design notes. The auto-build steps below apply to generated charts, not imported TJA sessions.

### Build path and source-of-truth

1. `scripts/run_tab.ps1` calls `taikolab.cli import-tab`.
2. `discover_song_files` must return the actual full mix as `main_audio` and isolated files as `StemPaths`. The importer parses the `.mid`, aligns a synthesized MIDI guide to the full audio with SyncToolbox, and refines drum timing from `stems/drums.wav` when available.
3. The aligned events, tempo regions, meter, audio filename, and stem paths are stored in `workspace/<song>/analysis.json`. `arrange_song` quantizes these events into courses using `configs/hori_default.yaml` and the configured preset (the wizard defaults to `punk_gallop`).
4. `tja_writer.py` serializes continuous BPM/meter timing, `OFFSET`, `#SCROLL`, and note rows, then copies the TJA and `WAVE` file to `YataiDON-Windows/Songs/<song>/`.

The MIDI is the chart-note and tempo source. Audio/stems position that MIDI on the recording; they do not supply a second set of chart notes. GP files are not consumed by the current importer, so never claim the GP was programmatically weighed against the MIDI.

Existing TJA editing is a separate path: the picker creates an in-memory imported session, the editor applies note/roll/balloon changes to the parsed chart baseline, and Save TJA writes back to the selected file with a first-save backup. It does not rerun alignment or the generated-chart build pipeline.

For quick regeneration from a verified cached analysis, use `.venv\\Scripts\\python.exe -m taikolab.cli arrange --song <song-id> --preset <preset>`. Use `run_tab.ps1` only when intentionally re-running MIDI/audio alignment. Do not pass a manual offset unless listening evidence calls for it.

## What was fixed

The existing Dumpweed override file had 240 custom downbeat markers spanning 0.489–142.147 seconds. Their median spacing was about 0.592 seconds, while the MIDI-derived measure grid was about 1.184 seconds. `build_measure_slots` prefers custom downbeats when at least two are present, so the arranger treated half-measures as full measures. The resulting chart had a BPM header of 405.371 and changes as high as 380 BPM.

Every other saved tap was consistent with the MIDI-derived downbeats: 120 taps at a median 1.184-second spacing, with 116 within 60 ms of a MIDI downbeat. The source analysis contains 121 MIDI downbeats.

To repair the build without discarding the original taps:

- Copied the original override data to [overrides.before-downbeat-repair.json](./workspace/dumpweed/overrides.before-downbeat-repair.json).
- Cleared only `custom_downbeats` in [overrides.json](./workspace/dumpweed/overrides.json). The seven section overrides and other settings were preserved.
- Rebuilt with the existing `taikolab.cli arrange --song dumpweed` path, using the cached `analysis.json`.
- Exported the chart to YataiDON.

The repaired [Dumpweed chart](./workspace/dumpweed/dumpweed.tja) now has four courses and 121 measures per course. Its header BPM is 202.686; emitted BPM changes are 192.678–208.587. `WAVE:audio_clean.wav` exists. The workspace chart and [YataiDON copy](C:/!CODE/YataiDON-Windows/Songs/dumpweed/dumpweed.tja) currently have identical SHA-256 hashes.

The configured blast threshold is already 215 BPM in [hori_default.yaml](./configs/hori_default.yaml), with the density condition in [section_builder.py](./src/taikolab/arranger/section_builder.py). That threshold was not the cause of the 400-BPM chart.

## Source usage and weighting

The current `run_tab.ps1` command passes `dumpweed.mid` to `import-tab`; it does not pass or read `dumpweed.gp` directly. If the MIDI was exported from the GP file, that GP content reaches the build through the MIDI.

The MIDI is the source of the chart's drum notes, bars, and tempo. Audio alignment does not vote between MIDI and GP: the importer aligns the MIDI timeline to the song audio. If file discovery finds a drum stem, its detected drum hits are timing references; otherwise the importer detects drum hits from the full mix. The full audio is also used for the alignment. Those detections help place MIDI events in time; they are not currently a separate competing source of chart notes.

For this Dumpweed analysis, the MIDI contributes 426 kicks, 295 snares, 89 crashes, 32 toms, 482 rides, and 200 hi-hats. The base mapper maps kicks to Don, snares and crashes to Ka, and ignores rides and hi-hats. The new advanced-course variation rule selectively uses existing ride hits as Ka accents in repeated phrases; it does not change the kick/snare mapping.

## Timing and TJA status

The known-good start is `OFFSET:-0.489`, copied from the chart whose start the user confirmed was correct. The user playtested the separate [Dumpweed Tempo Curve Test](C:/!CODE/YataiDON-Windows/Songs/TaikoLab_Dumpweed_Tempo_Curve_Fix_Test/dumpweed_tempo_curve_test.tja) and reported that it worked very well and completely fixed the timing issue. The earlier `OFFSET:0.000` tests changed the start and were not valid tempo tests.

The old production TJA's simulated measure grid ran about 2.5 seconds late by the end, so the issue was accumulated tempo/grid drift rather than a constant start offset. The timing correction is in [tja_writer.py](./src/taikolab/exporters/tja_writer.py), and the validated process is:

1. Keep the known-good chart offset; do not zero or recalibrate it to compensate for end-of-song drift. `render_tja_text` writes the sign-adjusted value (`OFFSET:-0.489` for this chart).
2. Simulate one continuous elapsed-time clock through every measure. Do not reset the clock at meter changes.
3. At each measure, calculate `drift = simulated_start - intended_start`. Positive drift means the chart is late, so shorten that measure's emitted duration; negative drift lengthens it. The correction is `drift * 0.25`, clamped to at most 25 ms per measure.
4. Derive BPM from the corrected measure duration. Emit a BPM change for the first measure, meter changes, or a difference of at least the configured deadzone. The normal CLI uses `smooth_bpm_deadzone: 2.5` from [default_settings.yaml](./configs/default_settings.yaml); the writer's 1.8 BPM default applies only to direct callers that omit the setting. Any residual drift remains visible to later measures rather than being hidden by resetting the clock.
5. Simulate the emitted BPM/meter map and fail export if maximum measure-start or final-end drift exceeds 100 ms. This is a guard against silently exporting a badly drifting chart, not a substitute for listening.

The corrected method passed static checks against both cached songs, and the user confirmed its in-game result on Dumpweed:

| Song | Measures | TJA offset | Max simulated grid drift | End error |
|---|---:|---:|---:|---:|
| Dumpweed | 121 | -0.489 s | about 33.1 ms | about +24.8 ms |
| B.Y.O.B. | 151 | -0.039 s | about 41.3 ms | about -1.8 ms |

The timing test used the production offset, same audio, and cached analysis; only the tempo map was changed. The user playtest accepted it. The new [Dumpweed Variety Test](C:/!CODE/YataiDON-Windows/Songs/TaikoLab_Dumpweed_Variety_Test/dumpweed_variety_test.tja) uses the same cached analysis and the CLI's configured 2.5 BPM deadzone. Its `OFFSET`, `BPM`, `#MEASURE`, and `#BPMCHANGE` command sequence was compared with the accepted Tempo Curve Test and matches exactly. The production [dumpweed.tja](./workspace/dumpweed/dumpweed.tja) and its YataiDON copy now have normalized `#SCROLL` directives, but retain the same notes, offset, BPM map, and measure boundaries. Keep future note-pattern edits on the same measure grid.

## What inputs are used for alignment

The importer reads the MIDI through `mido`; it does not parse the `.gp` file directly or compare GP and MIDI tracks. MIDI supplies the drum notes and measure/tempo grid. The importer also aligns a synthesized MIDI guide against the full audio with SyncToolbox. If a drum stem is discovered, its detected kick/snare transients refine that alignment; otherwise the full mix is transcribed for drum transients. Those audio/stem detections align MIDI events rather than supplying chart notes.

This means the pipeline currently uses MIDI plus audio/stems, but there is no independent GP-versus-MIDI validation. A `.gp` file can still be used as a manual reference or exported to MIDI before import. Do not assume the undocumented Songsterr weighting/alignment method is reproduced.

For The Leaving Song Part II, the MP3 is in `workspace/leavingsongpart2`; its historical `leavingsongpart2.mid` and `leavingsongpart2.gp` references are under `workspace/leavingsongpart2/sources/`. Demucs stems are generated and cached under `workspace/leavingsongpart2/stems`. The importer uses the MIDI, MP3-derived `audio_clean.wav`, and isolated drum stem; it still does not parse the GP directly.

## Click-overlay timing check

Three 30-second stereo diagnostics are available in [diagnostics/](./workspace/dumpweed/diagnostics/). They use the first 30 seconds of `audio_clean.wav`, with a loud low click for each MIDI kick and a higher click for each MIDI snare:

- [Reference timing (0 ms shift)](./workspace/dumpweed/diagnostics/dumpweed_click_reference_0ms.wav) uses cached aligned MIDI timestamps.
- [Clicks 489 ms earlier](./workspace/dumpweed/diagnostics/dumpweed_click_early_489ms.wav) and [489 ms later](./workspace/dumpweed/diagnostics/dumpweed_click_late_489ms.wav) test the two offset directions.

The user reported the 0 ms reference clicks align best with the real drums, and separately confirmed that the corrected tempo-map TJA now plays in time. This supports both the cached MIDI/audio alignment and the corrected TJA tempo-map serialization. Do not change global offset or tempo logic while editing note patterns.

The timing issue is accepted as fixed. Keep future note-pattern edits on the same measure grid; do not alter global offset, BPM map, or measure boundaries.

## LeavingsongPart2 build and validation

The user clarified that this song is slower alternative rock, not a standard punk gallop. Do not select `punk_gallop` just because earlier test songs used it. Genre presets affect note-source selection and drum mapping; actual BPM and timing continue to come from MIDI/audio alignment. A new [alt_rock.yaml](./configs/presets/alt_rock.yaml) profile keeps Easy, Normal, and Hard drum-led, uses a hybrid Oni course, maps kicks/toms to Don and snare/crash/ride to Ka, and ignores hi-hat chatter. The global default preset was not changed.

The first import exposed a stem-discovery bug. [stem_loader.py](./src/taikolab/ingest/stem_loader.py) enumerated the `stems` folder twice. On the first pass, the WAVs were classified as stems; on the second, their duplicate entries became non-stem candidates. Since WAVs outrank MP3s, the importer selected `bass.wav` as the song mix. That attempt aligned only 51.7% of kick/snare hits within 35 ms and the TJA writer correctly refused export at 115 ms drift. No chart was exported from that attempt.

The duplicate scan was removed and a regression test added in [test_ingest_dsp_and_pipeline.py](./tests/test_ingest_dsp_and_pipeline.py). After the fix, file discovery selected `theleavingsongpart2.mp3` as the full mix and `stems/drums.wav` as the drum reference. The MP3 was decoded to `audio_clean.wav`; the successfully aligned result reports 82.0% of MIDI kick/snare hits within ±35 ms.

The corrected analysis contains 77 chart measures and 19 tempo regions. The chart starts at 1.780 seconds, so the emitted header is `OFFSET:-1.780`; do not change this offset without listening to the click reference. The median/base BPM is 88.757, and the reported course BPM range is 77–194, reflecting the source tempo curve rather than a fixed punk gallop.

The existing 2.5 BPM deadzone alone allowed this song's serialized map to drift 251 ms. [tja_writer.py](./src/taikolab/exporters/tja_writer.py) now emits an otherwise-suppressed BPM change when retaining the active BPM would project more than 50 ms of error by the end of the current measure. The 100 ms export guard remains in place. This adaptive rule leaves Dumpweed's accepted tempo-command map unchanged: its current rendered map exactly matches the accepted [Dumpweed Variety Test](C:/!CODE/YataiDON-Windows/Songs/TaikoLab_Dumpweed_Variety_Test/dumpweed_variety_test.tja).

The generated [LeavingsongPart2 chart](./workspace/leavingsongpart2/leavingsongpart2.tja) is also exported to `C:/!CODE/YataiDON-Windows/Songs/leavingsongpart2/leavingsongpart2.tja`. Its Easy/Normal/Hard/Oni courses each contain 77 measures and 36 BPM changes. Simulating the emitted commands gives a maximum grid drift of 47.5 ms and final end error of about -4.9 ms. The workspace and simulator TJA hashes match, as do the `audio_clean.wav` copies.

A 30-second loud-click diagnostic is available at [leavingsongpart2_click_reference_0ms.wav](./workspace/leavingsongpart2/diagnostics/leavingsongpart2_click_reference_0ms.wav). It overlays low clicks on aligned MIDI kicks and higher clicks on aligned MIDI snares; the first 30 seconds contain 37 kick clicks and 13 snare clicks. This is a listening aid, not proof of alignment. The user has now playtested the chart and says the courses are good overall but the quiet intro is hard to play; the rebuilt intro and click overlay still await confirmation.

### Standardized highway speed

The accepted Dumpweed chart has `BPM:202.686` and `#SCROLL 0.500`, so its reference effective highway speed is about `101.343` (`BPM × SCROLL`). The new global `target_highway_speed` in [default_settings.yaml](./configs/default_settings.yaml) uses that value. `normalize_scroll_on_bpm_change` is now enabled by default, and [config.py](./src/taikolab/config.py) validates the setting.

[tja_writer.py](./src/taikolab/exporters/tja_writer.py) emits `#SCROLL = target_highway_speed / active_BPM` at BPM transitions. This changes only visual note movement; `OFFSET`, `#BPMCHANGE`, `#MEASURE`, note rows, and audio timestamps are unchanged. The feature is passed through both the CLI and UI export paths ([cli.py](./src/taikolab/cli.py), [server.py](./src/taikolab/ui/server.py)). A regression test verifies that the BPM×SCROLL product stays at the target while the timing-command sequence remains identical.

All 10 known TaikoLab-generated TJA files were brought to this standard: the three workspace charts (`byob`, `dumpweed`, `leavingsongpart2`), their three YataiDON exports, and four Dumpweed diagnostic/test exports. The eight pre-existing TJA files were migrated by changing/inserting only `#SCROLL` directives; every other line, including BPM, offset, meter, and note rows, was checked unchanged. At the time of the speed-only re-render, LeavingsongPart2's non-scroll lines were verified unchanged; the later quiet-guitar safeguard intentionally simplifies its first five measures as documented below. Each workspace/export pair has matching file hashes. Across all measure rows, emitted `BPM × SCROLL` is 101.251–101.423 (target 101.343; the small spread is from TJA's three-decimal scroll values). The rest of the YataiDON library was not touched.

The highway-speed change updates [default_settings.yaml](./configs/default_settings.yaml), [config.py](./src/taikolab/config.py), [tja_writer.py](./src/taikolab/exporters/tja_writer.py), [cli.py](./src/taikolab/cli.py), [server.py](./src/taikolab/ui/server.py), and the scroll regression in [test_exporter_and_arranger.py](./tests/test_exporter_and_arranger.py). The `alt_rock.yaml` preset and LeavingsongPart2 stem-discovery regression are also part of this work.

Validation completed:

- The latest focused suite after the quiet-guitar change: 28 passed across the arranger, ingest, and config tests.
- `git diff --check`: clean.
- Dumpweed accepted timing-command map: exact match (28 commands).
- LeavingsongPart2 timing simulation: 77 measures per course, max drift 47.5 ms.
- Pylance signature compatibility: all call sites for the modified TJA writer APIs are compatible.
- Ruff passed with known baseline findings suppressed. The unrestricted check still reports pre-existing lint findings on unchanged lines; those unrelated findings were not modified.

## Quiet guitar intro safeguard

The user reported that the crescendoing guitar at the start of LeavingsongPart2 produced too many hard-to-play notes across every course. The cause was guitar-onset injection: the intro has repeated detected guitar attacks but no MIDI drum hits. Imported MIDI analyses also lacked the `energy_curve` that the audio-only analysis path already generated.

`quiet_guitar_rms_threshold: 0.25` is a global setting in [default_settings.yaml](./configs/default_settings.yaml). For each measure, the arranger averages the 5 Hz guitar-stem RMS samples, normalized against that stem's peak. If guitar events and nonzero guitar energy exist, no drum hits are present, and that mean is below the threshold, the guitar detail is replaced by one Don at slot 0 (the measure downbeat). Drum-led measures retain their MIDI drum notes; silent measures or missing guitar-energy data are not treated as quiet music. Easy's ordinary two-measure phrase-rest rule is bypassed for these quiet guitar-only bars so the downbeat remains playable.

The MIDI import path now creates energy data, and CLI/UI arrangement backfill it from available stems for older cached analyses and save it into `analysis.json`. If no usable stems or no energy samples are available, the old arrangement behavior is preserved.

For LeavingsongPart2, the energy cache now contains 1,056 samples. Its first five measures have average guitar RMS values of 0.018, 0.060, 0.126, 0.180, and 0.228, all below the 0.25 threshold. Each course now has exactly one Don at slot 0 in those five measures. The next measure contains five MIDI drum hits and keeps its drum-driven pattern. The rebuilt chart retains `OFFSET:-1.780`, 77 measures and 36 BPM changes per course, and the 101.343 highway-speed target. Workspace and YataiDON TJA copies match.

This change updates [config.py](./src/taikolab/config.py), [section_builder.py](./src/taikolab/arranger/section_builder.py), [mapper_fusion.py](./src/taikolab/arranger/mapper_fusion.py), [hori_filter.py](./src/taikolab/arranger/hori_filter.py), [vocal_riff_tracker.py](./src/taikolab/ingest/vocal_riff_tracker.py), [tab_sync.py](./src/taikolab/ingest/tab_sync.py), [cli.py](./src/taikolab/cli.py), [server.py](./src/taikolab/ui/server.py), and the focused arranger/ingest/config tests.

Latest focused validation: 28 passed across `test_exporter_and_arranger.py`, `test_ingest_dsp_and_pipeline.py`, and `test_schemas_and_config.py`. Pylance reported no diagnostics in the changed arranger, ingest, CLI, or test files. The full suite was not rerun; the known tab-sync test/source mismatch below remains.

## Hard-chart detail baseline

Before the new pattern rules, Hard had 610 notes and Oni had 716. Hard already followed the kick/snare source well except for the repetitive ending.

## MIDI-grounded variety rules

The current arrangement code adds variation without changing any chart measure's start, end, BPM, or the accepted TJA offset:

- **Hard:** Detects runs of at least four consecutive measures with the same mapped drum pattern. Only a repeated run near the end of the song is embellished, so earlier Hard sections remain unchanged. Within that run, alternating offbeat positions are chosen only when a ride hit actually exists at that MIDI time. These selected ride events become Ka accents; kick and snare placements are left intact.
- **Oni:** Uses the same repeated-pattern detection across the song and a richer repeating selection cycle for MIDI ride accents. This gives repeated grooves different playable shapes rather than copying the Hard course or adding arbitrary timestamps.
- **Easy/Normal rolls:** Rolls replace only a rapid source-drum run, not a whole ordinary groove. A candidate needs at least five mapped drum hits in a span of at least the configured minimum roll duration, no gap above 180 ms, and a run of at least three consecutive hits from the same drum source. Easy and Normal are each capped at three such rolls per course, with at least four measures between selected rolls. The start/end markers remain on the existing quantized grid. Easy phrasing normally inserts a full measure rest after two active measures, except that low-energy guitar-only measures retain their single downbeat.
- **Timing invariant:** These changes modify note codes only. They do not shift events, add measures, alter measure boundaries, change BPM, or change `OFFSET`.

On the cached Dumpweed analysis, the variety test has 620 Hard notes (up from 610) and 780 Oni notes (up from 716). Hard ride accents appear only in the final repeated phrase; Oni uses varied accents in repeated phrases. Easy has three fill rolls in measures 38, 55, and 70; Normal has three in measures 15, 55, and 70; Hard and Oni do not receive these fill rolls. Easy retains 41 empty measures from its existing pause rule.

## Validation and known issue

- `tests/test_exporter_and_arranger.py`: 10 passed, including repeated-phrase variation, limited source-fill rolls, Easy rests, and the tempo-map regression tests.
- Focused timing/arranger regression run: 22 passed across `tests/test_tab_sync.py` and `tests/test_exporter_and_arranger.py`, including affine-warp selection, localized-fast-fill rejection, and partial-final-measure tempo/beat spacing.
- Real-song checks used temporary directories and existing source audio/MIDI: Everlong improved from 738/1,050 to 998/1,050 kick/snare matches within 35 ms; Police Truck kept DTW and no longer has the false 174–184 BPM ending. All His Suits remains inconclusive at the quiet/no-drum tail.
- Full suite: 53 passed. One existing Starlette/httpx deprecation warning was reported; no test failures.
- The current scoped Ruff check passes with known baseline codes ignored. An unrestricted check reports pre-existing `I001`, `PLR1730`, `RUF015`, `RUF046`, `UP035`, and `F841` findings on unchanged lines.
- `git diff --check` completed without whitespace errors. Git reported line-ending conversion warnings for modified files.
- Existing local edits in [tab_sync.py](./src/taikolab/ingest/tab_sync.py) were preserved. The duplicate stem scan in [stem_loader.py](./src/taikolab/ingest/stem_loader.py) was removed because it caused this import to choose a stem as the full mix. The prior [hori_filter.py](./src/taikolab/arranger/hori_filter.py) edits were retained and extended surgically for source-supported rolls. Do not discard unrelated local edits while continuing.

## Planning: repeated-phrase variety and MIDI preview

This section records the user's 2026-11-19 playtest feedback and remaining design work. Fill-roll placement has since been tightened: candidates are source-supported late-measure fills, and a roll is skipped unless there is at least an eighth-note of clearance before the next regular note. The wizard now defaults to the earlier Punk Gallop roll behavior (up to three Easy/Normal fill rolls, zero Hard/Oni) and exposes per-course caps.

### Confirmed observations

- Fill-roll clearance is now checked against later regular notes, including notes in following measures; rejected fills remain individual Don/Ka hits. The detector still looks for at least five drum events within a short span, no inter-hit gap above 180 ms, and a run of at least three events from one drum source. It spaces selected rolls by four measures.
- Legacy default caps are three Easy, three Normal, zero Hard, and zero Oni. The UI can explicitly raise or lower each course's cap; Hard/Oni roll insertion is therefore opt-in from the controls, apart from the existing blast/`chaos_roll` behavior.
- Current MIDI drum mapping deliberately maps kick to Don and snare to Ka. That is a useful readable baseline, but the user wants occasional color inversions and other source-supported differences, especially in Hard and Oni.
- Existing repeated-pattern detection is narrower than the user's idea of repeated song sections: it detects four or more adjacent measures with exactly identical one-measure drum signatures. It does not generally find a repeated four-bar phrase such as A-B-C-D followed later by A-B-C-D, or near-matching phrases elsewhere in the song.
- Existing Hard/Oni differences are not guaranteed. The current extra variation is limited to ride-hit accents on detected repeated bars (Hard only varies a late repeated run; Oni uses a broader slot cycle). If the MIDI has no usable ride events or no exact repeated bars, both courses can remain very similar.
- MIDI voice events are parsed, and the vocal mapper can create cadence patterns in sections configured to follow vocals. The current Hard drum-following path does not use vocal cadence as an independent phrase-variation layer. Any future vocal use should be a phrase-level cue, not a substitute for MIDI drum timing.

### Everlong phrase-end drift and outro timing experiment

Evidence now supports an alignment-map issue rather than a global offset issue for Everlong. Its source MIDI is a steady 158 BPM; the previous local DTW map matched 738 of 1,050 classified kick/snare events within 35 ms. A constrained affine candidate matched 998/1,050 (about 95%) and was selected only because it improved the score by more than the minimum required margin. The resulting analysis is a steady 158.02 BPM, including the final partial measure. Do not infer that every song should use an affine map: Police Truck's affine candidate did not beat its DTW match score and DTW remained selected.

Police Truck's source MIDI ends with 92 BPM bars; its late rapid snare cluster is too localized to represent a whole-bar tempo doubling. The previous cached analysis emitted 174.88 and 183.69 BPM at the end. Double-time detection now runs on raw MIDI timing and requires at least seven consecutive short gaps spanning at least half the bar. The new isolated validation retained an 84.33 BPM section from 133.1–138.7 seconds and a 91.64 BPM final section through the 144.97-second audio end; the false 174–184 BPM jump is gone.

The same validation found that a partial final measure was being assigned a false high BPM because its duration was clipped to the audio end before tempo calculation. The importer now extrapolates the existing warp at the final measure boundary, computes tempo from the full mapped measure, and retains the original beat spacing while emitting only beats inside the audio. This also removed Everlong's spurious 173 BPM final-measure reading.

All His Suits Are Torn remains a caution case: the MIDI is a constant 190 BPM and its last drum event is at 128.84 seconds, while the audio continues to about 138.4 seconds. The current timing run reports 79.1% kick/snare matches within 35 ms and no clear sustained slow-down in the final audio tail. Do not add chart notes or claim an outro tempo drop where the supplied MIDI has no drum events; use a click-overlay/playtest if the tail itself sounds slowed.

The user clarified that the idea is a MIDI-only pipeline: render the supplied MIDI to an audio file, then use that rendered audio to identify drum hits and build the chart, with no original recording. This is feasible, but rendering and then re-detecting hits is not the best way to preserve exact MIDI timing. A synth render preserves the MIDI events; an audio transcriber then has to infer those same onsets again and may add attack, classification, or MP3 encoder-delay errors. If the desired source is the MIDI, parse its drum events and tempo map directly and send those events into the arranger. A synthesized audio render can still be produced in parallel as a listening preview or for a user to verify track selection and sound. Prefer WAV as the internal render; create an MP3 only as a convenient playback copy. Neither approach can make MIDI that is quantized or inaccurate relative to the recording match the original performance. The existing full-song audio/MIDI-alignment path remains the separate choice when the chart should follow the actual recording.

### Recommended chart-variation design

1. **Make roll endings safe before adding more rolls.** Use a time-based clearance check against the next regular note, including notes in the next measure. A reasonable first default to test is at least one eighth-note of empty space after the roll end marker. If that gap cannot be preserved without removing a meaningful MIDI hit, do not convert that fill to a roll; keep the individual Don/Ka hits instead. Keep roll counts low and favor fills near phrase endings, consistent with the user's preference for only a few rolls.
2. **Detect repeated phrases, not only repeated bars.** Build rhythmic fingerprints for one-, two-, and four-measure windows from quantized MIDI events, preserving event class (kick/snare/tom/cymbal), position, and confidence. Compare phrases across the whole song, allowing small timing/velocity differences without treating unrelated riffs as identical. Mark the first phrase as the faithful reference and apply changes only on later repetitions.
3. **Use a controlled, deterministic variation palette.** On selected repeats, preserve the key downbeats and the original event timing, then choose a bounded change such as inverting Don/Ka colors on offbeats or a short subphrase, accenting actual ride/crash events, using MIDI double-snare/ghost-note patterns, or replacing a genuine end-of-phrase drum fill with a short roll. Avoid inventing a second Ka for a single snare hit unless there is supporting MIDI evidence or a deliberately approved style rule. Use a stable seed derived from the song/phrase so rerunning a build does not reshuffle the chart.
4. **Give Hard and Oni separate design targets.** Hard should stay close to the source groove and use a small number of clear, phrase-level variations. Oni should deliberately get more variants on eligible repeats—such as an additional color inversion, a source-supported double, or a more detailed fill—while respecting its NPS/gap limits. Do not just add random notes to force a count difference. Regression tests should prove that Oni is meaningfully different from Hard when repeated material supports it, that Hard retains the core motif, that unaffected measures remain unchanged, and that both courses keep identical timing grids.
5. **Use vocals carefully.** Existing vocal onsets can help identify phrase starts/ends, call-and-response gaps, or an accent opportunity in Oni. Treat them as a low-weight structural cue and retain MIDI drums as the primary source. Dense vocal syllables should not automatically become extra drum notes.

### Tabs/GP and audio-only boundaries

The current importer parses MIDI, not Guitar Pro (`.gp`) files. The MIDI already provides the principal drum events and timing; audio/stem analysis is primarily used to align those events and refine timing. Given the user's experience that audio-only charting usually fails, keep it as a fallback rather than the primary punk/alt-rock path.

Reintroducing GP/tab input is feasible as an optional enrichment path, particularly for identifying drum-track fills or phrase-end patterns that are missing from a MIDI. It should not silently override the chosen MIDI: first identify and validate the drum track, convert its events to the same timeline, compare it with MIDI and audio alignment, and report conflicts. The usefulness depends on the quality and format/version of each GP file, so this is a follow-up after phrase detection on MIDI has been evaluated. The location/interface of the user's existing browser custom charter is not established in this workspace; keep it as an edge-case/manual fallback for now, and assess an integration adapter only after the automatic path is stronger.

### MIDI-only chart and audio render (`--auto-midi`)

The user is considering a MIDI-only workflow: select a MIDI file, render its tracks to audio without the original song, and use that render to detect the drum hits for charting. This can produce a listenable MIDI-instrument rendition, but the chart should use the MIDI event data directly rather than transcribing the render back into note events. The MIDI already has the exact authored note positions, tempo map, and (where supplied) time signatures; audio onset detection would only estimate them again and can lose precision. A new MIDI-only chart path would bypass audio/MIDI DTW and feed the parsed drum events and MIDI timing directly into the arranger. The render is a companion preview to hear the selected tracks and arrangement, not the timing source.

MIDI contains instructions and timing, not instrument audio. TaikoLab currently synthesizes only a lightweight tone guide inside `tab_sync.py` for alignment; it does not provide a full General MIDI playback renderer. A usable rendition would need a local synthesizer and a legally redistributable or user-provided General MIDI soundfont; do not extract or depend on Songsterr's sounds. Render to WAV internally to avoid MP3 encoder-delay/priming ambiguity, and optionally make an MP3 playback copy. This mode will follow the MIDI's authored timing, which may not match the recorded performance if the MIDI is quantized or inaccurate. Keep it separate from the existing audio-aligned `-auto` workflow, which is the mode intended to follow a supplied recording. Decide whether `--auto-midi` labels the chart, preview, or both before implementation.

The user owns FL Studio 2026 and asked whether its sounds can supply the renderer. Image-Line's [DirectWave manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/plugins/DirectWave.htm) says the full DirectWave version imports SoundFont2 (`.sf2`), while DirectWave Player is limited to DirectWave formats; full DirectWave availability depends on edition/purchase. Do not assume the user's installation includes an `.sf2` file. A possible general-purpose GM candidate to evaluate is [GeneralUser GS](https://www.schristiancollins.com/generaluser.php), from its author; it is a broad GM set rather than a dedicated rock-only bank. Test its overdriven/distortion guitar, bass, and drum-kit patches against a sample MIDI, and confirm the archive's license before bundling or redistributing it. [FluidSynth](https://www.fluidsynth.org/wiki/Download/) is a separate synthesizer with documented Windows support and could be evaluated for a local automated renderer.

## Automatic-charting backlog (not the current task)

These are parked follow-ups from the automatic charting work. The immediate priority remains the user's playtest of the existing-TJA editor; do not start these items unless the user asks to resume charting work.

1. Regenerate Everlong and Police Truck into backed-up workspace copies and playtest the new maps/clicks before promoting them. Do not alter the accepted global offset based on the old cached TJA outputs.
2. Playtest the generated charts with the new controls, especially roll clearance, source selection, density, and Hard/Oni differences. Current roll placement requires late-measure source-supported fills and an eighth-note clearance.
3. Extend repetition detection from adjacent identical single bars to repeated one-to-four-measure phrases using MIDI first. Test against the user's examples: Kill the Poor, Holiday in Cambodia, and Voyeur; retain BYOB and Slipknot's Eyeless as difficult-case checks rather than assuming one preset fits every song.
4. Add deterministic, evidence-bounded variation in stages: selected Don/Ka inversions and actual MIDI-supported doubles/fills, then a stronger Oni variant policy than Hard. Confirm timing and measure grids do not change.
5. Consider a separate local MIDI-only preview after choosing a synthesizer/soundfont and resolving its license/distribution approach. Keep it diagnostic and do not treat it as a replacement for audio/MIDI alignment.
6. Revisit optional GP/tab enrichment only if MIDI phrase/fill detection is insufficient. Keep the existing browser charter as a manual edge-case tool until its location and integration format are known.
7. Preserve audio-only mode as a fallback, not the default for songs with usable MIDI. Do not use global timing changes to compensate for chart-variation problems.
8. Run the full suite before treating the timing implementation as complete; the focused timing and arranger tests currently pass.




TAIKOLAB — HANDOVER ADDENDUM
Windows deployment validation (GTX 1080) and RTX 3050 handoff preparation
Date: 2026-11-21

SCOPE
This addendum records only the deployment and Git work performed in the current session. It is intended to be appended to the existing TaikoLab development handover. It does not replace the main architecture or feature documentation.

1. OBJECTIVE AND CURRENT STATUS

The objective was to create and validate a Windows deployment approach that installs TaikoLab's Python dependencies into a project-local virtual environment, supports NVIDIA CUDA through a locally installed PyTorch build, and can be transferred to a second Windows PC with an RTX 3050.

GTX 1080 test status: PASSED for environment installation, CUDA access, a direct GPU computation, the Python test suite, and application launch/use as reported by the user.

RTX 3050 test status: NOT YET RUN. A source ZIP has been prepared for testing on the second PC. No RTX 3050 installation results are available yet.

GitHub status: NOTHING FROM THIS DEPLOYMENT WORK HAS BEEN PUSHED. The deployment changes are committed locally on the deployment-validation branch only.

2. PATHS AND ARTIFACTS

Original development repository:
  C:\!CODE\TaikoLab

Separate GTX 1080 deployment test copy:
  C:\!CODE\TaikoLab-Deployment-Test-20261121-115615

Prepared transfer archive reported by user:
  TaikoLab-deployment-validation-7b52601.zip

The test copy and original repository were kept separate. The test copy received its own uv executable, managed Python runtime, virtual environment, package installation and deployment logs. The original repository's existing .venv was not used by the deployment installer and was not replaced.

The deployment test folder was created by copying the project into a new uniquely named sibling folder. The copy operation excluded .git, .venv, venv, env, workspace, Python bytecode/cache folders, pytest/ruff caches, build and dist outputs.

3. GTX 1080 ENVIRONMENT THAT WAS VALIDATED

Machine GPU:
  NVIDIA GeForce GTX 1080
  VRAM reported by nvidia-smi: 8192 MiB (8 GiB)

NVIDIA driver:
  582.66

nvidia-smi driver-reported CUDA compatibility version:
  13.0

Deployment tooling/runtime:
  uv: 0.13.9 (x86_64 Windows)
  Managed CPython: 3.12.16, Windows x86_64
  Project virtual environment: .venv inside the deployment-test folder

PyTorch runtime observed from the deployment .venv:
  torch: 2.10.0+cu126
  torch.version.cuda: 12.6
  torch.cuda.is_available(): True
  torch.cuda.get_device_name(0): NVIDIA GeForce GTX 1080

A direct CUDA tensor/matrix operation completed successfully:
  Input tensors: 2048 x 2048
  Result device: cuda:0
  Result shape: (2048, 2048)
  User-visible result: GPU computation successful: cuda:0 (2048,2048)

Note: nvidia-smi showing CUDA Version 13.0 is the driver's reported CUDA compatibility level. The installed PyTorch wheel was built for CUDA 12.6; torch.version.cuda reported 12.6 and CUDA execution worked.

4. DEPENDENCY AND INSTALLATION ISSUES RESOLVED DURING THE TEST

4.1 ADTOF package source

The original optional dependency was:
  "adtof-pytorch"

The normal package index could not resolve a distribution by that name. The deployment copy was changed to a direct Git dependency:
  "adtof-pytorch @ git+https://github.com/xavriley/ADTOF-pytorch.git"

This correction was subsequently integrated into the deployment-validation branch's pyproject.toml.

4.2 PyTorch CUDA package index selection

The deployment uses the official PyTorch CUDA 12.6 wheel index and PyPI as the additional index. uv initially selected PyPI as the first index containing torch and did not consider the requested CUDA build from the PyTorch index. Adding the following option resolved that index-selection issue:
  --index-strategy unsafe-best-match

The tested constraints file contains:
  torch==2.10.0+cu126
  torchaudio==2.10.0+cu126

The installer dependency command uses:
  --index-strategy unsafe-best-match
  --index-url https://download.pytorch.org/whl/cu126
  --extra-index-url https://pypi.org/simple
  --constraint deployment/gpu-constraints.txt
  -e ".[audio,ml,ui,desktop,tab]"

4.3 Test-only dependencies

The deployment runtime installation did not include the dev extra. pytest and ruff were therefore installed directly into the deployment .venv for test execution:
  pytest: 9.2.0
  ruff: 0.17.6

Initial test collection then failed in three test modules because the installed Starlette TestClient implementation required the httpx2 package. httpx2 was installed into the test .venv. The original repository's pyproject.toml was then updated to declare "httpx2" in the dev optional-dependency group, so a future dev install can reproduce the test prerequisites.

4.4 Optional dependency groups installed in the deployment copy

The tested deployment install requested all declared runtime groups:
  audio, ml, ui, desktop, tab

5. TEST RESULTS ON THE GTX 1080 DEPLOYMENT COPY

GPU diagnostic:
  PyTorch CUDA available: True
  GPU identified: NVIDIA GeForce GTX 1080

Direct GPU computation:
  PASSED

Pytest:
  86 passed in 19.58 seconds

The test suite command used:
  powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run_tests.ps1"

The wizard was launched from the deployment copy using:
  powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run_wizard.ps1"

The wizard opened. The user subsequently reported that the application/workflow worked well in the test copy. The conversation did not record a detailed per-stage checklist or individual song identifiers, so do not infer additional measured results beyond that user confirmation.

6. GIT BRANCH AND COMMIT HISTORY

Repository remote:
  origin https://github.com/Reg0lino/TaikoLab.git

At the start of this work, local master was one commit ahead of origin/master:
  fd1ccdb — "more efficient on cpu and gpu more customization options for the user"

A new branch was created from that commit:
  deployment-validation

A recovery branch was also created at the same commit:
  backup/master-before-deployment-validation-20261121

Local master was then reset to the remote-tracking branch:
  master / origin/master: 91fb139 — "v0.1 custom charting after automatic ingest"

The deployment integration was committed on deployment-validation:
  7b52601 — "Add isolated Windows deployment setup"

Therefore deployment-validation contains the pre-existing application improvement commit fd1ccdb plus deployment commit 7b52601, based on origin/master commit 91fb139. The local master branch remains at 91fb139. The backup branch retains fd1ccdb.

No commits were pushed to GitHub during this session.

At the last recorded git status, the working branch was deployment-validation and the only remaining untracked source-root file was:
  ?? setup_environment.ps1

That root setup_environment.ps1 file was already untracked before this deployment integration began. It was deliberately not staged or committed, and was left unchanged. The existing run_app.bat and scripts/run_wizard.ps1 were also left unchanged.

7. DEPLOYMENT INTEGRATION COMMIT CONTENTS

Commit 7b52601 contains six files changed (329 insertions, 2 deletions):

Modified:
  .gitignore
  pyproject.toml

Added:
  START_TAIKOLAB.bat
  deployment/check_env.ps1
  deployment/gpu-constraints.txt
  scripts/setup_deployment.ps1

pyproject.toml changes:
  - Replaced the unresolved ADTOF PyPI requirement with the direct Git dependency listed above.
  - Added httpx2 to the dev optional-dependency group.

.gitignore changes add exclusions for deployment-local tools, Python runtime, uv cache, deployment logs, deployment venv ownership marker, deployment backups and temporary helper scripts.

scripts/setup_deployment.ps1:
  - Bootstraps uv into a project-local .tools directory if needed.
  - Uses a project-local .python managed-Python directory and .uv-cache.
  - Installs managed Python 3.12 and creates a project-local .venv when no .venv exists.
  - Writes .taikolab-managed-venv after it creates the venv.
  - Refuses to proceed if .venv already exists without that ownership marker, to avoid taking over an existing development environment.
  - Installs the audio, ml, ui, desktop and tab extras using the CUDA 12.6 index and deployment/gpu-constraints.txt.
  - Checks for WebView2 and offers its installer if it is not detected.
  - Invokes deployment/check_env.ps1 and can launch scripts/run_wizard.ps1.

START_TAIKOLAB.bat invokes scripts/setup_deployment.ps1 with -Launch.

deployment/check_env.ps1 reports Python version, nvidia-smi details, selected Python package availability, PyTorch CUDA status and a small CUDA computation, and WebView2 detection.

deployment/gpu-constraints.txt pins torch and torchaudio to 2.10.0+cu126.

8. VALIDATION PERFORMED ON THE COMMITTED INTEGRATION FILES

After creating the integration files:
  - PowerShell parser validation reported syntax OK for scripts/setup_deployment.ps1.
  - PowerShell parser validation reported syntax OK for deployment/check_env.ps1.
  - git diff --check produced no output (no whitespace errors).
  - Python tomllib successfully parsed pyproject.toml and displayed the project name and optional dependencies, including the ADTOF Git dependency and httpx2 in dev.

Important distinction:
  The earlier generated installer in the separate GTX 1080 test copy was exercised successfully. The newly integrated and committed scripts/setup_deployment.ps1 in commit 7b52601 was syntax-checked but had NOT yet been run through a fresh end-to-end installation from the committed ZIP at the time of this handover. Its first clean end-to-end test is the planned RTX 3050 test. Treat this as pending validation, not as already proven.

9. RTX 3050 TEST HANDOFF — NEXT ACTIONS

The user reports having the ZIP:
  TaikoLab-deployment-validation-7b52601.zip

It was intended to be created from the committed deployment-validation branch using git archive, so it should represent the tracked files at commit 7b52601 and should not include .git, the GTX .venv, managed Python, uv cache or other untracked files. The archive's contents were not independently inspected in this session.

On the RTX 3050 PC:
  1. Extract the ZIP into a new, empty test folder (do not extract over an existing TaikoLab checkout).
  2. Keep the music and MIDI test files on the external drive or copy them into a separate test-data location; do not place personal workspace/audio data into the Git repository unless that is already the user's established workflow.
  3. Run START_TAIKOLAB.bat from the extracted project folder.
  4. Allow the installer to create its own Python 3.12 runtime and .venv. Do not copy the GTX 1080 .venv.
  5. If installation fails, capture the full console output and deployment-logs\setup.log.
  6. Verify nvidia-smi detects the RTX 3050.
  7. Run the deployment check or an equivalent Python check and verify torch.cuda.is_available() is True and torch.cuda.get_device_name(0) identifies the RTX 3050.
  8. Run a small CUDA tensor operation.
  9. Launch the wizard and test the user's actual audio/MIDI workflow, including any ML stages and chart output they normally rely on.
  10. Record any model-weight download, external executable, GPU, WebView2, or inference issues. These were not exhaustively validated on the second PC yet.

Do not push or merge deployment-validation into master until the RTX 3050 test is complete and the committed installer has passed a clean installation test.

10. IMPORTANT DO-NOT-ASSUME NOTES FOR THE NEXT ITERATION

  - The GTX 1080 deployment test passed, but the RTX 3050 has not yet been tested.
  - The committed installer in scripts/setup_deployment.ps1 has syntax validation only; its clean-install behavior still needs the RTX 3050 ZIP test.
  - The pytest result of 86 passed was obtained after manually installing pytest, ruff and httpx2 in the deployment test .venv. The committed pyproject.toml now declares httpx2 under dev, but the runtime installer intentionally installs runtime extras rather than dev tools.
  - The root setup_environment.ps1 remains untracked and was deliberately excluded from commit 7b52601.
  - run_app.bat and scripts/run_wizard.ps1 were not changed by this deployment integration.
  - No GitHub push or merge occurred in this session.
  - The existing GTX 1080 deployment-test folder should be retained as a known-working reference until the RTX 3050 test is complete.

END OF ADDENDUM


## HANDOVER ADDENDUM — Arranger Rules, Cymbal/Section Upgrades & GPU/Alignment Strategy

Updated: 2026-11-23 (Continuation after `deployment-validation` / `7b52601` baseline)

### 1. What Was Implemented Since the Deployment Addendum

1. **Groove Triple-Stroke Pass (`src/taikolab/arranger/groove_triples.py`, `schemas.py`, `section_builder.py`, `difficulty.py`, `wizard.py`, `index.html`, `app.js`):**
   - Added a post-ergonomics pass (`apply_groove_triples`) that injects Taiko-style 16th-note triples (`d-d-d _ k`, `k-k-k _ k`, reverse `k _ d-d-d`, and alternating `d-k-d`/`k-d-k`) into sparse repeating `4/4` rock/punk beats (`3–6` hits/bar, no existing adjacent 16ths). Dense fast skate-punk gallops (e.g., NOFX `10`-note/bar beats) are automatically skipped.
   - Uses relative pocket scanning so pickup-shifted grooves (e.g., *Blink-182 - Aliens Exist* at slots `2, 4, 6, 14`) are detected instead of requiring notes on slots `0, 4, 8`.
   - Protects Beat 1 downbeats (`slot 0` stays `DON` when anchored on `DON`).
   - Replaced fixed triple counts with **`0–100%` Groove Triple Density** per course (`Easy=0%`, `Normal=15%`, `Hard=45%`, `Oni=70%`), paired with:
     - **`Groove Triple Ending Ramp` (`0–100%`, default `50%`):** Deterministically weights selection so songs start sparser and build toward the final choruses/ending.
     - **Decoupled per-course Complexity:** `Normal=15%`, `Hard=25%` (mostly same-color triples), `Oni=80%` (high alternating/dual-pocket complexity).
     - **`Groove Triple Max BPM` (`120–300`, default `235`):** Hard/Oni use `<= max_bpm`; Normal uses `<= max(120, max_bpm - 15)`.
   - **Test status at this milestone:** All **90 tests** passed and *Aliens Exist* arranged cleanly (`Easy 226`, `Normal 628`, `Hard 1052`, `Oni 1177`).

2. **Fill Roll / Balloon Exit Pause & Course Caps (`hori_filter.py`, `section_builder.py`, `wizard.py`, `app.js`):**
   - Blocked automatic `chaos_roll` blast-folding on `Easy` and `Normal` (fixing *Aliens Exist* `Easy` getting 9 accidental blast rolls).
   - Updated Wizard/Console default fill-roll caps to **`Easy=1, Normal=4, Hard=5, Oni=6`**.
   - Updated `_find_fill_roll_span` clearance in `hori_filter.py` to enforce a `>= max(220 ms, 3/16 bar)` exit pause before the next measure's downbeat, trimming roll `end_slot` backward (and clearing trailing intra-bar slots) instead of silently dropping Oni/Hard rolls. `chaos_roll` also leaves a `>= 220 ms` exit pause.

3. **Pass 1 — Solo Cymbal & Quiet Hi-Hat Rescue (`mapper_drums.py`, `section_builder.py`):**
   - Quiet time-keeping bars (`<= 2` core kick/snare/tom/crash hits and `>= 2` hi-hats/rides) now preserve `KAT` time-keeping on `Normal` (quarter-beat pocket), `Hard`, and `Oni` (up to 8ths), while keeping `Easy` minimal.
   - Active bars rescue isolated solo cymbals (`ride`, open hi-hat `pitch=46`, and high-velocity `hihat`) only when clear of any core drum hit.
   - **Pending verification:** After the initial 95-test run hit 2 failures (`test_4bar_repeated_phrases...` and `test_quiet_timekeeping...`), a 2-line fix was supplied (`_SOLO_CYMBAL_CLEARANCE_SEC = 0.250` and restricting `Normal` quiet hi-hats to `(s_idx % beat_step) == 0`), **but test re-run confirmation is still pending** in the next thread.

4. **Pass 2 — Multi-Source Section Combos, Difficulty Checklist, Mark In/Out & `Auto-Split` (IN PROGRESS / PARTIALLY APPLIED):**
   - **Already applied on disk:**
     - `schemas.py` (`SectionDifficultyRule`, expanded `FollowMode`: `drums_vocals`, `vocals_guitar`, `all_balanced`), `section_builder.py`, `mapper_drums.py`, `mapper_vocals.py`, `mapper_fusion.py`.
     - `server.py` (`POST /api/songs/{song_id}/sections/auto-split` detecting bar-aligned Intro/Verse/Chorus/Break blocks from energy/vocal/tempo/drum transitions).
     - `index.html`, `styles.css`, and the first half of `app.js` (`markInSec`/`markOutSec`, `KeyI`/`KeyO`, `difficulty_rules` inspector sync).
   - **Still pending completion in the next thread:**
     - The final event-handler block in `app.js` (`btnMarkIn`, `btnMarkOut`, `btnCreateMarkedSection`, `btnAutoSplitSections`, `secSrc*` checkboxes, and `btnConfirmApplySection` checklist modal) + the two Pass 2 unit tests (`test_per_difficulty_section_rule...` and `test_auto_split_sections_endpoint...`) + full `run_tests.ps1` verification.
     - First action in next thread: inspect `git status --short`, verify the 2-line Pass 1 `mapper_drums.py` fix is present, finish the `app.js` tail + Pass 2 tests, and run `.\scripts\run_tests.ps1`.

---

### 2. How MIDI-Only Alignment Works Today (and Why Results Differ Without Demucs)

In `src/taikolab/ingest/tab_sync.py` (`import_midi_aligned`):
1. **SyncToolbox Chroma+DLNCO DTW still runs in MIDI-only (`use_stems=False`) mode:**
   - `_parse_midi_unified()` parses MIDI bars, tempo changes, drums, vocals, and guitars, then `_synthesize_midi_guide()` renders sustained pitch tones + drum clicks.
   - `_align_via_synctoolbox()` runs **CPU-only** `synctoolbox` Chroma+DLNCO DTW between that synthesized MIDI guide and the full mix (`audio_clean.wav`), fixing `(0,0)` intro/outro boundary lag.
2. **Where `ADTOF` and Demucs change the result:**
   - DTW alone can drift locally by `30–80 ms` in heavy/quiet sections. To refine and validate it, `tab_sync.py` runs 6-second window drum nudging and `_select_drum_alignment_warp()` (DTW vs. drum-validated affine warp) against detected `real_drums`.
   - **With Demucs (`use_stems=True`):** `real_drums` comes from `ADTOF + 3-band DSP` run on the **clean isolated `stems/drums.wav`**.
   - **Without Demucs (`use_stems=False`):** `real_drums` comes from `run_adtof_transcription(main_audio, device="cpu")` run on the **noisy full mix**. Distorted guitars, vocals, and bass mask kicks/snares and trigger false onsets, weakening both the 6s window snap and the affine-vs-DTW decision—which is why alignment feels worse without stems even though DTW still ran.
   - Note: `ADTOF` was benchmarked on the GTX 1080 at **`1.63s` on CPU vs. `4.10s` on CUDA**, so `ADTOF` now defaults to CPU (zero VRAM load).

---

### 3. Next-Thread Goals: Reducing CPU/GPU Load Without Crashing the PC

The GTX 1080 (`8 GB VRAM`, WDDM display GPU sharing VRAM with Windows DWM, VS Code, and Chromium/WebView2) hits **Windows WDDM TDR resets** (multiscreen glitch → black screen → GPU driver restart → CUDA `illegal memory access`) under sustained full-song `htdemucs` Transformer passes, while CPU-only `htdemucs` takes `~8–9 minutes` per song (`43.85s` per `15s` clip).

Concrete methods to evaluate and implement next:
1. **CPU Percussive Separation (`librosa.effects.hpss`) + `drum_dsp.py` (Zero GPU, ~2–4s):**
   - Instead of `htdemucs` for MIDI alignment, run `librosa.effects.hpss` on `audio_clean.wav` (strips sustained harmonic guitar/synth/vocal bleed) and feed the percussive stem into `ADTOF (CPU) + 3-band DSP`. Tests whether alignment lock approaches Demucs quality with **0 MB VRAM** and **< 5s CPU time**.
2. **Partial "Anchor-Only" Demucs Windows (`3 × 15s` instead of full song):**
   - Benchmark showed `15s` `htdemucs` on GTX 1080 completes safely in **`1.48s` (`616 MB` VRAM)**. Separating only 2–3 short 15s windows (intro, mid-song, outro) gives clean `stems/drums.wav` anchors for affine/DTW validation in `< 5s` GPU time without sustained WDDM queue starvation.
3. **Lighter Separation Model / Half-Precision / Pacing (`mdx_q` / `FP16`):**
   - If full stems are ever needed on GPU, test pure-convolutional `mdx` / `mdx_q` (avoids heavy `htdemucs` Cross-Transformer attention kernels), `torch.autocast("cuda", dtype=torch.float16)`, `shifts=0`, and longer inter-window yields—or offload heavy full-song Demucs exclusively to the second PC (RTX 3050).
4. **CPU Thread Caps (`torch.set_num_threads` / `OMP_NUM_THREADS`):**
   - Cap CPU thread count during `ADTOF` / `SyncToolbox` / `HPSS` so background builds do not peg 100% CPU and freeze the UI/audio.
5. **Local 4-Bar Drum-Stem Auto-Repair:**
   - Extend `_print_sync_audit()` in `tab_sync.py` so any 4-bar island where `|median_err| > 30 ms` against percussive/anchor transients automatically snaps locally rather than relying solely on one global DTW-vs-affine choice.