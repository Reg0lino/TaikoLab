param(
    [switch]$Launch
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot

$Tools = Join-Path $Root ".tools"
$PythonDir = Join-Path $Root ".python"
$Venv = Join-Path $Root ".venv"
$Logs = Join-Path $Root "deployment-logs"
$Marker = Join-Path $Root ".taikolab-managed-venv"

$Uv = Join-Path $Tools "uv.exe"
$Python = Join-Path $Venv "Scripts\python.exe"

# Protect existing development environments.
if ((Test-Path $Venv) -and -not (Test-Path $Marker)) {
    throw @"
An existing .venv was found, but it was not created by the TaikoLab
deployment installer.

To protect your development environment, installation has stopped.

Use a clean Git clone or a separate deployment folder.
No existing virtual environment has been modified.
"@
}

foreach ($Required in @(
    (Join-Path $Root "pyproject.toml"),
    (Join-Path $Root "deployment\gpu-constraints.txt")
)) {
    if (-not (Test-Path $Required)) {
        throw "Required file missing: $Required"
    }
}

New-Item -ItemType Directory -Path $Tools -Force | Out-Null
New-Item -ItemType Directory -Path $PythonDir -Force | Out-Null
New-Item -ItemType Directory -Path $Logs -Force | Out-Null

$LogFile = Join-Path $Logs "setup.log"
Start-Transcript -Path $LogFile -Append | Out-Null

try {
    Set-Location $Root

    # Keep uv, Python and caches local to this project.
    $env:UV_PYTHON_INSTALL_DIR = $PythonDir
    $env:UV_CACHE_DIR = Join-Path $Root ".uv-cache"
    $env:UV_INSTALL_DIR = $Tools
    $env:UV_NO_MODIFY_PATH = "1"

    # Download uv locally if necessary.
    if (-not (Test-Path $Uv)) {
        Write-Host "Downloading uv..."

        $Installer = Join-Path $Tools "install-uv.ps1"

        Invoke-WebRequest `
            -Uri "https://astral.sh/uv/install.ps1" `
            -OutFile $Installer `
            -UseBasicParsing

        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Installer

        if (-not (Test-Path $Uv)) {
            throw "uv installation failed."
        }
    }

    # Create a dedicated managed Python environment.
    if (-not (Test-Path $Venv)) {
        Write-Host "Installing Python 3.12..."

        & $Uv python install 3.12
        if ($LASTEXITCODE -ne 0) {
            throw "Python installation failed."
        }

        Write-Host "Creating isolated virtual environment..."

        & $Uv venv --python 3.12 $Venv
        if ($LASTEXITCODE -ne 0) {
            throw "Virtual environment creation failed."
        }

        # Record ownership immediately after successful creation.
        Set-Content -LiteralPath $Marker -Value (Get-Date -Format o) -Encoding UTF8
    }

    if (-not (Test-Path $Python)) {
        throw "Deployment Python executable was not found."
    }

    # Install the application and all declared runtime extras.
    # PyTorch uses the tested CUDA 12.6 build.
    Write-Host ""
    Write-Host "Installing TaikoLab dependencies..." -ForegroundColor Cyan

    & $Uv pip install --python $Python `
        --index-strategy unsafe-best-match `
        --index-url "https://download.pytorch.org/whl/cu126" `
        --extra-index-url "https://pypi.org/simple" `
        --constraint (Join-Path $Root "deployment\gpu-constraints.txt") `
        -e ".[audio,ml,ui,desktop,tab]"

    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed."
    }

    # Check WebView2.
    $WebViewFound = $false

    $WebViewPaths = @(
        "${env:ProgramFiles(x86)}\Microsoft\EdgeWebView\Application",
        "$env:ProgramFiles\Microsoft\EdgeWebView\Application"
    )

    foreach ($Path in $WebViewPaths) {
        if (Test-Path $Path) {
            $Runtime = Get-ChildItem -Path $Path -Filter "msedgewebview2.exe" -Recurse -ErrorAction SilentlyContinue |
                Select-Object -First 1

            if ($Runtime) {
                $WebViewFound = $true
                break
            }
        }
    }

    if (-not $WebViewFound) {
        Write-Warning "Microsoft Edge WebView2 Runtime was not detected."

        $Answer = Read-Host "Download and install WebView2 now? (Y/N)"

        if ($Answer -match '^(Y|YES)$') {
            $Bootstrapper = Join-Path $Tools "MicrosoftEdgeWebView2Setup.exe"

            Invoke-WebRequest `
                -Uri "https://go.microsoft.com/fwlink/p/?LinkId=2124703" `
                -OutFile $Bootstrapper `
                -UseBasicParsing

            Start-Process -FilePath $Bootstrapper `
                -ArgumentList "/silent", "/install" -Wait
        }
    }

    # Run diagnostics.
    Write-Host ""
    Write-Host "Checking the deployment environment..."

    $CheckScript = Join-Path $Root "deployment\check_env.ps1"

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $CheckScript

    if ($LASTEXITCODE -ne 0) {
        throw "Environment diagnostics failed."
    }

    # Launch the existing wizard.
    if ($Launch) {
        Write-Host ""
        Write-Host "Launching TaikoLab..."

        $Wizard = Join-Path $Root "scripts\run_wizard.ps1"

        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Wizard

        if ($LASTEXITCODE -ne 0) {
            throw "TaikoLab exited with an error."
        }
    }
    else {
        Write-Host "Environment setup completed." -ForegroundColor Green
    }
}
catch {
    Write-Host ""
    Write-Host "SETUP FAILED:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "See deployment-logs\setup.log."
    exit 1
}
finally {
    Stop-Transcript | Out-Null
}