$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $Root
& "$Root\.venv\Scripts\python.exe" -m taikolab.ui.wizard
