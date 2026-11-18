# TaikoLab

Modular, 100% local Punk / Hardcore / Metal to OpenTaiko (`.tja`) chart generator, built for real drumsticks on a Hori Taiko USB drum.

## Architecture Principles
- **No external AI APIs**: Everything runs locally on your PC.
- **Strict layer isolation**: `arranger` and `exporters` are pure Python (no ML/GPU dependencies).
- **Cached analysis**: Heavy audio processing saves `analysis.json` once; rule tweaks & UI rebuilds take < 1 second.

## Starter Scripts
- `.\scripts\check_env.ps1` - Verify Python, TaikoLab package, and GTX 1080 CUDA
- `.\scripts\run_tests.ps1` - Run test suite & architecture boundary checks
- `.\scripts\run_analyze.ps1 -Song "byob"` - Analyze stems/audio into `analysis.json`
- `.\scripts\run_build.ps1 -Song "byob"` - Build `.tja` from cached `analysis.json`
- `.\scripts\run_ui.ps1` - Launch local Director's Console browser UI