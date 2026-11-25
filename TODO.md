# TaikoLab Development TODO

## High Priority / Next Up

### 1. Robust Multi-Step Undo / Redo in Manual Chart Editor
- **Problem:** The Undo button (`Ctrl+Z`) is currently unreliable for chart edits (moving, deleting, pasting, roll resizing, color flips, grid warps).
- **Goal:** Ensure reliable, deterministic Undo/Redo supporting at least 5+ (up to 100) consecutive edit steps across all note, roll, balloon, and barline actions.
- **Technical Notes:** Audit `saveUndoPoint()`, `restoreEditState()`, `applyOptimisticNotePatch()`, and `editSeq` response dropping in `src/taikolab/ui/static/app.js` so cleared keys and partial `rebuildChart` snapshots never fall out of sync.

### 2. Variable Section Speeds (`#SCROLL`) Testing & Tuning
- **Goal:** Playtest and refine per-section relative highway speed multipliers (`0.25x–3.00x`) in the Section Customizer Matrix and Section Inspector.
- **Details:** Verify smooth `#SCROLL` transitions in YataiDON between slower breakdowns and faster choruses, and consider exposing a quick relative speed multiplier in the bottom Section Inspector alongside the Section Matrix.

### 3. Instrument / Guitar Mapping Rule Refinement
- **Goal:** Upgrade `src/taikolab/arranger/mapper_fusion.py` and onset extraction so instrument-led sections capture unique rhythmic variations and riff syncopation rather than repetitive beat/downbeat patterns.

### 4. Vocal Pipeline & Adaptive Phrasing Playtest Tuning
- **Goal:** Test `Alternating 2-Bar`, `Alternating 4-Bar`, `Adaptive`, and `Selective Parts` across diverse rock/punk/metal songs on physical drumsticks in YataiDON.
- **Details:** Fine-tune adaptive breath/pause detection and lead-in transitions as needed based on real playtest feel.

### 5. RTX 3050 Gaming PC Deployment & GPU Validation
- **Goal:** Clone repo onto the RTX 3050 machine, run `START_TAIKOLAB.bat` for clean `uv` + Python 3.12 + CUDA 12.6 installation, and verify `Balanced GPU (Anchor Stems)` and `Full GPU Stems (Demucs)` modes.
