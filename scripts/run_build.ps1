param(
    [Parameter(Mandatory=$true)][string]$Song
)
$ErrorActionPreference = "Stop"
& "$PSScriptRoot\..\.venv\Scripts\python.exe" -m taikolab.cli arrange --song $Song