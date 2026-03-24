param(
    [string]$PythonExe = "python",
    [string]$AppName = "PDFInvoiceRenamer"
)

$ErrorActionPreference = "Stop"

Write-Host "==> Using Python: $PythonExe"
Write-Host "==> App name: $AppName"

if (-not (Test-Path ".venv")) {
    Write-Host "==> Creating virtual environment (.venv)"
    & $PythonExe -m venv .venv
}

$venvPython = Join-Path ".venv" "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "Could not find venv python at $venvPython"
}

Write-Host "==> Upgrading pip"
& $venvPython -m pip install --upgrade pip

Write-Host "==> Installing project dependencies"
& $venvPython -m pip install -r requirements.txt

Write-Host "==> Installing PyInstaller"
& $venvPython -m pip install pyinstaller

Write-Host "==> Building one-file GUI executable"
& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onefile `
    --name $AppName `
    gui.py

$distExe = Join-Path "dist" "$AppName.exe"

if (Test-Path $distExe) {
    Write-Host "==> Build complete: $distExe"
} else {
    throw "Build finished but executable not found at $distExe"
}
