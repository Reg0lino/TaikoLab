$ErrorActionPreference = "Continue"

$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $Root ".venv\Scripts\python.exe"

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " TaikoLab Deployment Diagnostics"
Write-Host "=========================================="

if (-not (Test-Path $Python)) {
    Write-Warning "Deployment Python was not found."
    exit 1
}

Write-Host ""
Write-Host "Python:"
& $Python --version

Write-Host ""
Write-Host "NVIDIA driver and GPU:"

$Nvidia = Get-Command nvidia-smi -ErrorAction SilentlyContinue

if ($Nvidia) {
    & nvidia-smi
}
else {
    Write-Warning "nvidia-smi not found. NVIDIA GPU access is not verified."
}

Write-Host ""
Write-Host "Python packages and CUDA:"

$DiagnosticCode = @"
import importlib.util
import torch

modules = [
    "numpy", "pydantic", "yaml", "librosa", "soundfile",
    "scipy", "fastapi", "uvicorn", "keyring", "mutagen",
    "pretty_midi", "synctoolbox"
]

for name in modules:
    try:
        print(name + ":", "installed" if importlib.util.find_spec(name) else "missing")
    except Exception as exc:
        print(name + ": diagnostic error:", exc)

print()
print("PyTorch:", torch.__version__)
print("CUDA build:", torch.version.cuda)
print("CUDA available:", torch.cuda.is_available())

if torch.cuda.is_available():
    print("GPU:", torch.cuda.get_device_name(0))
    a = torch.rand(1024, 1024, device="cuda")
    b = a @ a
    torch.cuda.synchronize()
    print("GPU computation: PASS", tuple(b.shape))
else:
    print("WARNING: PyTorch cannot access an NVIDIA CUDA GPU.")
"@

& $Python -c $DiagnosticCode

if ($LASTEXITCODE -ne 0) {
    Write-Warning "Python diagnostics returned an error."
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "WebView2:"

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

if ($WebViewFound) {
    Write-Host "WebView2 detected." -ForegroundColor Green
}
else {
    Write-Warning "WebView2 was not detected."
}

Write-Host ""
Write-Host "Diagnostics completed."