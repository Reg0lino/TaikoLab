param(
    [Parameter(Mandatory=$true)][string]$Song,
    [Parameter(Mandatory=$true)][string]$Tab,
    [string]$Title = "",
    [string]$Artist = "Unknown",
    [string]$Preset = "punk_gallop",
    [string]$Audio = ""
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SongDir = Join-Path $Root (Join-Path "workspace" $Song)
New-Item -ItemType Directory -Force -Path $SongDir | Out-Null

function Copy-InputWithBackup {
    param([string]$Source, [string]$Destination)
    if (Test-Path -LiteralPath $Destination) {
        $sourceHash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
        $destinationHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
        if ($sourceHash -ne $destinationHash) {
            $SourcesDir = Join-Path $SongDir "sources"
            New-Item -ItemType Directory -Force -Path $SourcesDir | Out-Null
            $timestamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
            $extension = [System.IO.Path]::GetExtension($Destination)
            $backupName = [System.IO.Path]::GetFileNameWithoutExtension($Destination) + "_" + $timestamp + $extension
            Copy-Item -LiteralPath $Destination -Destination (Join-Path $SourcesDir $backupName)
        }
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

$TabSource = (Resolve-Path -LiteralPath $Tab).Path
if ([System.IO.Path]::GetExtension($TabSource).ToLowerInvariant() -notin @(".mid", ".midi")) {
    throw "Tab must be a MIDI file (.mid or .midi). Guitar Pro files are not parsed."
}
$TabDestination = Join-Path $SongDir "input.mid"
if ([System.IO.Path]::GetFullPath($TabSource) -ne [System.IO.Path]::GetFullPath($TabDestination)) {
    Copy-InputWithBackup -Source $TabSource -Destination $TabDestination
}

$argsList = @("-m", "taikolab.cli", "import-tab", "--song", $Song, "--tab", $TabDestination, "--artist", $Artist, "--preset", $Preset)
if ($Title -ne "") {
    $argsList += @("--title", $Title)
}

if ($Audio -ne "") {
    $AudioSource = (Resolve-Path -LiteralPath $Audio).Path
    $AudioExtension = [System.IO.Path]::GetExtension($AudioSource).ToLowerInvariant()
    if ($AudioExtension -notin @(".mp3", ".ogg", ".wav", ".flac")) {
        throw "Audio must be an MP3, OGG, WAV, or FLAC file."
    }
    $AudioDestination = Join-Path $SongDir ("audio" + $AudioExtension)
    if ([System.IO.Path]::GetFullPath($AudioSource) -ne [System.IO.Path]::GetFullPath($AudioDestination)) {
        Copy-InputWithBackup -Source $AudioSource -Destination $AudioDestination
    }
}

Set-Location -LiteralPath $Root
& "$PSScriptRoot\..\.venv\Scripts\python.exe" @argsList