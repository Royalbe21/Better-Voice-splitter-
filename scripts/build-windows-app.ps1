param(
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$distDir = Join-Path $root "dist"

Set-Location $root

if (-not (Test-Path $venvPython)) {
    py -3.11 -m venv .venv
}

if (-not $SkipDependencyInstall) {
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r requirements.txt
    & $venvPython -m pip install pyinstaller
}

& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name "Easy Voice Splitter" `
    app.py

Write-Host ""
Write-Host "Build complete:"
Write-Host (Join-Path $distDir "Easy Voice Splitter\Easy Voice Splitter.exe")
Write-Host ""
Write-Host "To build the installer, open installer\EasyVoiceSplitter.iss with Inno Setup Compiler."
