param(
    [Parameter(Mandatory=$true)][string]$Song,
    [Parameter(Mandatory=$true)][string]$Tab,
    [string]$Title = "",
    [string]$Artist = "Unknown",
    [string]$Preset = "punk_gallop"
)
$ErrorActionPreference = "Stop"
$argsList = @("-m", "taikolab.cli", "import-tab", "--song", $Song, "--tab", $Tab, "--artist", $Artist, "--preset", $Preset)
if ($Title -ne "") {
    $argsList += @("--title", $Title)
}
& "$PSScriptRoot\..\.venv\Scripts\python.exe" @argsList