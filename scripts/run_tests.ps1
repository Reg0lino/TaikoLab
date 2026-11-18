$ErrorActionPreference = "Stop"
& "$PSScriptRoot\..\.venv\Scripts\python.exe" -m pytest -v
if ($LASTEXITCODE -ne 0) {
    throw "Pytest failed with exit code $LASTEXITCODE"
}