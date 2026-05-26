# Easy Voice Splitter

Easy Voice Splitter is a Windows desktop app for separating an audio file into vocals, instrumental audio, and speaker-specific vocal clips.

## Features

- Guided Windows UI with live processing logs
- Setup checker for Python packages, FFmpeg, and Hugging Face login
- Saved settings between runs
- Optional clean output folder for every run
- Adjustable minimum speaker clip length
- Adjustable clip padding
- Adjustable silence gap between combined speaker clips
- Cancel button for long processing runs
- Open Output shortcut after a run finishes

## What it creates

- `exports/<input>_vocals.wav`
- `exports/<input>_instrumental.wav`
- `speaker_segments/<speaker>/<input>_<speaker>_###.wav`
- `combined_speakers/<input>_<speaker>_combined.wav`
- `speaker_segments_summary.csv`
- `speaker_segments_summary.txt`
- raw Demucs output under `stems/`

## Quick setup

Run:

```powershell
.\scripts\setup-windows.ps1
```

The setup script creates `.venv`, installs Python packages, and warns if FFmpeg is missing.

You still need to log in to Hugging Face after setup:

```powershell
.\.venv\Scripts\huggingface-cli.exe login
```

Also accept model access for:

- `pyannote/speaker-diarization-3.1`

## Manual setup

### 1. Create a Python environment

Python 3.11 is recommended.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 2. Install FFmpeg

`ffmpeg-python` is a Python helper package, but the real FFmpeg program must also be installed.

```powershell
winget install --id Gyan.FFmpeg -e
```

After installing, close and reopen PowerShell so Windows refreshes PATH.

### 3. Log in to Hugging Face

```powershell
huggingface-cli login
```

Also accept model access for:

- `pyannote/speaker-diarization-3.1`

## Run the app

Use the launcher:

```powershell
.\Easy Voice Splitter.cmd
```

Or run Python directly:

```powershell
.\.venv\Scripts\python.exe app.py
```

When the app opens, click **Check Setup** first. It verifies Python packages, FFmpeg, and Hugging Face login before processing starts.

## Processing options

- **New folder for each run** keeps every output in its own timestamped folder.
- **Minimum clip seconds** skips tiny speaker fragments.
- **Clip padding ms** adds a little audio before and after each speaker clip.
- **Combined gap ms** controls the silence between clips in each combined speaker file.

## Build a Windows app

The build script creates a windowed `.exe` with PyInstaller.

```powershell
.\scripts\build-windows-app.ps1
```

The built app will be here:

```text
dist\Easy Voice Splitter\Easy Voice Splitter.exe
```

## Build an installer

1. Install Inno Setup Compiler.
2. Run `.\scripts\build-windows-app.ps1`.
3. Open `installer\EasyVoiceSplitter.iss` in Inno Setup Compiler.
4. Click **Compile**.

The installer will be created under:

```text
installer\output\EasyVoiceSplitterSetup.exe
```

The installer adds Start Menu shortcuts and can optionally add a desktop shortcut.

## Notes

- The first run may take longer because Demucs and pyannote may download model files.
- Speaker model access is checked when the pyannote model loads.
- Use **Cancel** to stop Demucs while it is running or to prevent later export stages from continuing.
