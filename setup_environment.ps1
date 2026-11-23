
$ErrorActionPreference = "Stop"

Write-Host "Setting up TaikoLab environment..."

# Verify Python
python --version

# Create virtual environment if one doesn't exist
if (!(Test-Path ".venv")) {
    python -m venv .venv
}

# Activate virtual environment
& .\.venv\Scripts\Activate.ps1

# Upgrade pip
python -m pip install --upgrade pip setuptools wheel

# Install TaikoLab and desktop dependencies
python -m pip install -e ".[ui,desktop]"

# Verify installed dependencies
python -m pip check

Write-Host "TaikoLab environment setup complete."
