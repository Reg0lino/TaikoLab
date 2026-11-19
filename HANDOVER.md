# TaikoLab Handover

Updated: 2026-11-18

## Current goal

Dumpweed timing was playtested and accepted, and its separate variety test was judged good enough for now. Its notes, offset, and BPM map are unchanged; only its highway scroll was normalized to the shared target. The Leaving Song Part II uses the alt-rock profile and shared highway speed. A quiet-guitar safeguard was added and the chart rebuilt; the new intro still needs the user's playtest.

## Gemini handoff: project map and workflow

This section is intended to give a fresh AI instance enough context to continue without relying on this chat. Read this file before changing charts. Treat generated chart/audio files as user data: do not overwrite or delete unrelated songs in the large YataiDON library.

```text
TaikoLab/
|-- configs/
|   |-- default_settings.yaml       Global offsets, tempo deadzone, highway speed, quiet-guitar threshold
|   |-- hori_default.yaml           Course density, note gaps, roll ergonomics
|   `-- presets/
|       |-- punk_gallop.yaml        Punk chart-source and drum-mapping rules
|       |-- alt_rock.yaml           Added for The Leaving Song Part II
|       |-- mathcore.yaml
|       `-- powerviolence.yaml
|-- incoming_midi/
|   |-- leavingsongpart2.mid        MIDI used by the importer
|   `-- leavingsongpart2.gp         Reference only; importer does not parse GP
|-- scripts/
|   |-- run_tab.ps1                 MIDI import, audio alignment, arrangement, export
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
|   |-- ui/server.py                UI arrangement/export path
|   |-- cli.py                      CLI arrangement/export path
|   |-- config.py                   Settings and preset models
|   `-- schemas.py
|-- tests/
|   |-- test_exporter_and_arranger.py
|   |-- test_ingest_dsp_and_pipeline.py
|   `-- test_tab_sync.py
|-- workspace/
|   |-- byob/                       Cached analysis and generated chart
|   |-- dumpweed/                   Cached analysis, overrides, generated chart
|   `-- leavingsongpart2/
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

### Build path and source-of-truth

1. `scripts/run_tab.ps1` calls `taikolab.cli import-tab`.
2. `discover_song_files` must return the actual full mix as `main_audio` and isolated files as `StemPaths`. The importer parses the `.mid`, aligns a synthesized MIDI guide to the full audio with SyncToolbox, and refines drum timing from `stems/drums.wav` when available.
3. The aligned events, tempo regions, meter, audio filename, and stem paths are stored in `workspace/<song>/analysis.json`. `arrange_song` quantizes these events into courses using `configs/hori_default.yaml` and the selected genre preset.
4. `tja_writer.py` serializes continuous BPM/meter timing, `OFFSET`, `#SCROLL`, and note rows, then copies the TJA and `WAVE` file to `YataiDON-Windows/Songs/<song>/`.

The MIDI is the chart-note and tempo source. Audio/stems position that MIDI on the recording; they do not supply a second set of chart notes. GP files are not consumed by the current importer, so never claim the GP was programmatically weighed against the MIDI.

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

For The Leaving Song Part II, the MP3 is in `workspace/leavingsongpart2`; `incoming_midi` has both `leavingsongpart2.mid` and `leavingsongpart2.gp`. Demucs stems are generated and cached under `workspace/leavingsongpart2/stems`. The importer uses the MIDI, MP3-derived `audio_clean.wav`, and isolated drum stem; it still does not parse the GP directly.

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
- Full suite: 29 passed, 1 failed. `test_isolated_drum_warp_tracks_smooth_drift` imports `_estimate_isolated_drum_warp`, which is absent from the current modified [tab_sync.py](./src/taikolab/ingest/tab_sync.py). This is a pre-existing alignment-test/source mismatch, unrelated to the timing serializer or chart-variation changes.
- The current scoped Ruff check passes with known baseline codes ignored. An unrestricted check reports pre-existing `I001`, `PLR1730`, `RUF015`, `RUF046`, `UP035`, and `F841` findings on unchanged lines.
- `git diff --check` completed without whitespace errors. Git reported line-ending conversion warnings for modified files.
- Existing local edits in [tab_sync.py](./src/taikolab/ingest/tab_sync.py) were preserved. The duplicate stem scan in [stem_loader.py](./src/taikolab/ingest/stem_loader.py) was removed because it caused this import to choose a stem as the full mix. The prior [hori_filter.py](./src/taikolab/arranger/hori_filter.py) edits were retained and extended surgically for source-supported rolls. Do not discard unrelated local edits while continuing.

## Suggested next steps

1. Play the rebuilt [LeavingsongPart2 chart](C:/!CODE/YataiDON-Windows/Songs/leavingsongpart2/leavingsongpart2.tja) in OpenTaiko, especially the first five measures on each course, and confirm the single downbeat per bar is easier to play.
2. The [LeavingsongPart2 click reference](./workspace/leavingsongpart2/diagnostics/leavingsongpart2_click_reference_0ms.wav) remains available if timing needs another check. If it is off, note whether it is wrong at the start or drifts later; do not change the global offset to compensate for a tempo-map problem.
3. After the intro is confirmed, pause chart work and gather the user's remaining requirements for the drop-a-song automatic build tool. That tool has not been started.
4. Choose future song presets by style instead of assuming punk. Continue to keep MIDI/audio timing separate from genre-specific chart rules.
5. Address the known `tab_sync.py` test/source mismatch before treating the full suite as green.
