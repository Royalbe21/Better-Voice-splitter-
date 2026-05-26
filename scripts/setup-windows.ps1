$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

Set-Location $root

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' was not found. Install Python 3.11, then run this script again."
}

if (-not (Test-Path $venvPython)) {
    py -3.11 -m venv .venv
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning "FFmpeg was not found in PATH."
    Write-Warning "Install it with: winget install --id Gyan.FFmpeg -e"
    Write-Warning "After installing FFmpeg, close and reopen PowerShell."
}

Write-Host ""
Write-Host "Setup complete."
Write-Host "Next:"
Write-Host "1. Run: .\.venv\Scripts\huggingface-cli.exe login"
Write-Host "2. Accept access for pyannote/speaker-diarization-3.1 on Hugging Face."
Write-Host "3. Launch: .\Easy Voice Splitter.cmd"
