# TaikoLab

Modular, 100% local Punk / Hardcore / Metal to OpenTaiko (`.tja`) chart generator, built for real drumsticks on a Hori Taiko USB drum.

## Architecture Principles
- **No external AI APIs**: Everything runs locally on your PC.
- **Strict layer isolation**: `arranger` and `exporters` are pure Python (no ML/GPU dependencies).
- **Cached analysis**: Heavy audio processing saves `analysis.json` once; rule tweaks & UI rebuilds take < 1 second.

## Starter Scripts
- `.\scripts\check_env.ps1` - Verify Python, TaikoLab package, and GTX 1080 CUDA
- `.\scripts\run_tests.ps1` - Run test suite & architecture boundary checks
- `.\scripts\run_wizard.ps1` - Open the dark desktop wizard to stage inputs and create/export an `-auto` chart
- `.\run_app.bat` - Double-click launcher for the TaikoLab desktop app
- `.\run_build.bat [song-id]` - Rebuild a chart from its cached analysis; prompts for the workspace song ID if omitted
- `.\scripts\run_analyze.ps1 -Song "byob"` - Analyze stems/audio into `analysis.json`
- `.\scripts\run_build.ps1 -Song "byob"` - Build `.tja` from cached `analysis.json`
- `.\scripts\run_ui.ps1` - Launch local Director's Console browser UI

The desktop wizard stores each new song's selected audio, optional MIDI, analysis, stems, diagnostics, and TJA under one `workspace\<artist-title-auto>\` folder, using standardized `audio.<ext>` and `input.mid` working names. It remembers separate last-used folders for audio and MIDI; until a MIDI folder has been chosen, its picker starts in the remembered audio folder. `run_tab.ps1` follows the same per-song layout. The old `incoming_midi\` folder is no longer part of the build flow. GP files are not parsed in this version.

The wizard defaults to the prior Punk Gallop charting behavior without showing a genre selector: vocals for Easy/Normal, drums for Hard, hybrid for Oni, and the original Punk Gallop drum mapping. Each slider has its own **Reset** button. Defaults are highway speed 101.343, big-note threshold 0.88, quiet-guitar threshold 0.25, 100% density for each course, no added repeat-color inversion, and fill-roll caps of 3/3/0/0 for Easy/Normal/Hard/Oni. Before building, you can adjust the source followed by each course (automatic, drums, balanced, vocals, or guitar), course density/roll caps, repeat variation, and quiet-intro treatment. These settings are saved per song and do not alter MIDI/audio alignment. Highway speed changes scroll only; source selection, density, rolls, and variation affect the chart arrangement. Simulator replacement backups are stored in `_TaikoLabBackups` beside (one folder above) the selected `Songs` folder, not inside it.

The Director's Console is also the manual chart editor. Choose **Open Manual Editor** in the desktop wizard at any time; a new chart build is not required. Use **Open Existing TJA…** in the editor toolbar to load a prior chart without rebuilding from audio/MIDI; saving updates that file and creates a `.tja.bak` backup first. The editor provides audio/stem playback, a read-only MIDI ghost lane, note/roll/balloon editing, snapping, undo/redo, and keyboard shortcuts. On Windows, install the editor and wizard dependencies with `.\.venv\Scripts\python.exe -m pip install -e ".[ui,desktop]"`; the Microsoft Edge WebView2 Runtime is also required for embedding. To run the editor separately in a browser, use `.\scripts\run_ui.ps1`. The root `run_build.bat` is a convenience launcher for the cached-analysis build script; it does not import or analyze new audio/MIDI files.

Install the wizard's metadata and secure-key dependencies with `.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"`. The wizard can use Audiometa's free authenticated search API; its key is stored in the Windows credential store, and only artist/title search text is sent (never song files). If the API is unavailable, title and artist remain editable manual inputs.