import csv
import importlib.util
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "Easy Voice Splitter"
PYANNOTE_MODEL = "pyannote/speaker-diarization-3.1"
DEMUCS_MODEL = "mdx_extra_q"
SETTINGS_PATH = Path(os.environ.get("APPDATA", Path.home())) / APP_TITLE / "settings.json"


class SimpleVoiceSplitterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("980x720")
        self.root.minsize(900, 640)

        self.input_file = tk.StringVar()
        self.output_folder = tk.StringVar()
        self.status_text = tk.StringVar(value="Ready. Start with Step 1.")
        self.setup_summary = tk.StringVar(value="Setup not checked yet.")

        self.export_vocals = tk.BooleanVar(value=True)
        self.export_instrumental = tk.BooleanVar(value=True)
        self.export_speaker_clips = tk.BooleanVar(value=True)
        self.export_combined_speakers = tk.BooleanVar(value=True)
        self.export_summary = tk.BooleanVar(value=True)
        self.create_job_folder = tk.BooleanVar(value=True)
        self.min_clip_seconds = tk.StringVar(value="0.30")
        self.clip_padding_ms = tk.StringVar(value="150")
        self.combined_gap_ms = tk.StringVar(value="500")

        self.cancel_event = threading.Event()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.active_process: subprocess.Popen[str] | None = None
        self.worker: threading.Thread | None = None
        self.last_output_path: Path | None = None

        self._build_ui()
        self._load_settings()
        self.root.after(100, self._drain_log_queue)
        self.root.after(250, self._run_startup_check)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=APP_TITLE, font=("Segoe UI", 20, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Simple workflow: choose audio, choose output, pick what you want created, then start.",
            foreground="#555",
        ).grid(row=1, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_text, foreground="#1f4e79").grid(row=0, column=1, rowspan=2, sticky="e")

        notebook = ttk.Notebook(outer)
        notebook.grid(row=1, column=0, sticky="nsew")

        self.main_tab = ttk.Frame(notebook, padding=14)
        self.settings_tab = ttk.Frame(notebook, padding=14)
        self.log_tab = ttk.Frame(notebook, padding=14)
        notebook.add(self.main_tab, text="Run Job")
        notebook.add(self.settings_tab, text="Setup & Settings")
        notebook.add(self.log_tab, text="Logs")

        self._build_main_tab()
        self._build_settings_tab()
        self._build_log_tab()

    def _build_main_tab(self) -> None:
        tab = self.main_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(4, weight=1)

        step1 = ttk.LabelFrame(tab, text="Step 1 — Choose the audio file", padding=12)
        step1.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        step1.columnconfigure(1, weight=1)
        ttk.Label(step1, text="Pick the recording you want to split. WAV, MP3, M4A, FLAC, and OGG are supported.").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Button(step1, text="Choose Audio File", command=self.pick_file).grid(row=1, column=0, sticky="ew", padx=(0, 10))
        ttk.Label(step1, textvariable=self.input_file, foreground="#333").grid(row=1, column=1, sticky="ew")

        step2 = ttk.LabelFrame(tab, text="Step 2 — Choose where results will be saved", padding=12)
        step2.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        step2.columnconfigure(1, weight=1)
        ttk.Label(step2, text="The app creates a clean job folder containing vocals, speaker clips, combined files, and reports.").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Button(step2, text="Choose Output Folder", command=self.pick_output).grid(row=1, column=0, sticky="ew", padx=(0, 10))
        ttk.Label(step2, textvariable=self.output_folder, foreground="#333").grid(row=1, column=1, sticky="ew")

        step3 = ttk.LabelFrame(tab, text="Step 3 — Select what you want created", padding=12)
        step3.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        step3.columnconfigure(0, weight=1)
        step3.columnconfigure(1, weight=1)

        self._option_row(step3, 0, 0, "Clean vocals WAV", "Creates the isolated voice track from Demucs.", self.export_vocals)
        self._option_row(step3, 0, 1, "Background / instrumental WAV", "Creates the no-vocals track for comparison.", self.export_instrumental)
        self._option_row(step3, 1, 0, "Individual speaker clips", "Cuts separate WAV clips for each detected speaker segment.", self.export_speaker_clips)
        self._option_row(step3, 1, 1, "One combined file per speaker", "Creates one longer file for each speaker with silence between clips.", self.export_combined_speakers)
        self._option_row(step3, 2, 0, "CSV and TXT speaker report", "Creates timestamps, speaker labels, clip numbers, and file paths.", self.export_summary)
        self._option_row(step3, 2, 1, "New folder for each run", "Keeps every job organized in its own timestamped folder.", self.create_job_folder)

        actions = ttk.LabelFrame(tab, text="Step 4 — Start processing", padding=12)
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        for column in range(5):
            actions.columnconfigure(column, weight=1)
        self.start_button = ttk.Button(actions, text="Start Voice Split", command=self.start)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.open_output_button = ttk.Button(actions, text="Open Last Output", command=self.open_last_output, state="disabled")
        self.open_output_button.grid(row=0, column=2, sticky="ew", padx=(0, 8))
        self.check_button = ttk.Button(actions, text="Check Setup", command=self.check_setup_clicked)
        self.check_button.grid(row=0, column=3, sticky="ew", padx=(0, 8))
        self.progress = ttk.Progressbar(actions, mode="indeterminate")
        self.progress.grid(row=0, column=4, sticky="ew")

        help_box = ttk.LabelFrame(tab, text="What happens when you click Start?", padding=12)
        help_box.grid(row=4, column=0, sticky="nsew")
        help_text = (
            "1. Demucs separates the recording into clean vocals and background audio.\n"
            "2. Pyannote scans the clean vocal track and detects different speakers.\n"
            "3. The app exports the files you selected above into the output folder.\n\n"
            "Tip: For conversations, interviews, and phone recordings, the mdx_extra_q Demucs model is used because it worked better in your testing."
        )
        ttk.Label(help_box, text=help_text, justify="left").pack(anchor="w")

    def _option_row(self, parent: ttk.Frame, row: int, column: int, title: str, description: str, variable: tk.BooleanVar) -> None:
        box = ttk.Frame(parent, padding=(4, 4))
        box.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else 12, 0), pady=5)
        ttk.Checkbutton(box, text=title, variable=variable).pack(anchor="w")
        ttk.Label(box, text=description, foreground="#666", wraplength=410).pack(anchor="w", padx=(24, 0))

    def _build_settings_tab(self) -> None:
        tab = self.settings_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)

        setup = ttk.LabelFrame(tab, text="Setup tools", padding=12)
        setup.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        for column in range(4):
            setup.columnconfigure(column, weight=1)
        ttk.Label(setup, text="Use these buttons when packages, FFmpeg, or Hugging Face login need attention.").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        ttk.Button(setup, text="Check Setup", command=self.check_setup_clicked).grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(setup, text="Install / Update Requirements", command=self.install_requirements).grid(row=1, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(setup, text="Hugging Face Login", command=self.huggingface_login).grid(row=1, column=2, sticky="ew", padx=(0, 8))
        ttk.Button(setup, text="Open Project Folder", command=self.open_project_folder).grid(row=1, column=3, sticky="ew")
        ttk.Label(setup, textvariable=self.setup_summary, foreground="#1f4e79").grid(row=2, column=0, columnspan=4, sticky="w", pady=(10, 0))

        processing = ttk.LabelFrame(tab, text="Processing settings", padding=12)
        processing.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for column in range(6):
            processing.columnconfigure(column, weight=1)
        ttk.Label(processing, text="Minimum clip seconds").grid(row=0, column=0, sticky="w")
        ttk.Entry(processing, textvariable=self.min_clip_seconds, width=10).grid(row=0, column=1, sticky="w", padx=(8, 20))
        ttk.Label(processing, text="Clip padding ms").grid(row=0, column=2, sticky="w")
        ttk.Entry(processing, textvariable=self.clip_padding_ms, width=10).grid(row=0, column=3, sticky="w", padx=(8, 20))
        ttk.Label(processing, text="Combined gap ms").grid(row=0, column=4, sticky="w")
        ttk.Entry(processing, textvariable=self.combined_gap_ms, width=10).grid(row=0, column=5, sticky="w", padx=(8, 0))
        ttk.Label(
            processing,
            text="Lower minimum clip = more small fragments. More padding = less chopped speech. Combined gap controls silence between clips.",
            foreground="#666",
            wraplength=850,
        ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(8, 0))

        info = ttk.LabelFrame(tab, text="Current model settings", padding=12)
        info.grid(row=2, column=0, sticky="nsew")
        ttk.Label(info, text=f"Demucs voice isolation model: {DEMUCS_MODEL}").pack(anchor="w", pady=2)
        ttk.Label(info, text=f"Speaker detection model: {PYANNOTE_MODEL}").pack(anchor="w", pady=2)
        ttk.Label(info, text=f"Settings saved to: {SETTINGS_PATH}", foreground="#666").pack(anchor="w", pady=2)

    def _build_log_tab(self) -> None:
        tab = self.log_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        self.log_text = tk.Text(tab, height=18, wrap="word", state="disabled", background="#f7f7f7", foreground="#222", relief="solid", borderwidth=1)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        ttk.Button(tab, text="Clear Logs", command=self._clear_log).grid(row=1, column=0, sticky="w", pady=(10, 0))

    def pick_file(self) -> None:
        file_path = filedialog.askopenfilename(title="Choose an audio file", filetypes=[("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg"), ("All files", "*.*")])
        if file_path:
            self.input_file.set(file_path)

    def pick_output(self) -> None:
        folder = filedialog.askdirectory(title="Choose output folder")
        if folder:
            self.output_folder.set(folder)

    def open_last_output(self) -> None:
        target = self.last_output_path or Path(self.output_folder.get() or ".")
        if not target.exists():
            messagebox.showwarning("Output folder", "The output folder does not exist yet.")
            return
        os.startfile(target)

    def open_project_folder(self) -> None:
        os.startfile(Path.cwd())

    def install_requirements(self) -> None:
        req = Path("requirements.txt")
        if not req.exists():
            messagebox.showerror("Missing requirements", "requirements.txt was not found in this folder.")
            return
        self._run_background_command([sys.executable, "-m", "pip", "install", "-r", str(req)], "Installing requirements...")

    def huggingface_login(self) -> None:
        command = [sys.executable, "-m", "huggingface_hub.commands.huggingface_cli", "login"]
        self._log("Opening Hugging Face login in a new PowerShell window...")
        try:
            subprocess.Popen(["powershell", "-NoExit", "-Command", " ".join(command)])
        except Exception as exc:
            messagebox.showerror("Hugging Face Login", f"Could not start login command: {exc}")

    def _run_background_command(self, command: list[str], status: str) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("Busy", "A task is already running.")
            return
        self.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.start(10)
        self._set_status(status)
        self.worker = threading.Thread(target=lambda: self._command_worker(command), daemon=True)
        self.worker.start()

    def _command_worker(self, command: list[str]) -> None:
        try:
            self._log("Command: " + " ".join(command))
            self.active_process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            assert self.active_process.stdout is not None
            for line in self.active_process.stdout:
                if line.strip():
                    self._log(line.rstrip())
                if self.cancel_event.is_set():
                    self.active_process.terminate()
                    raise CanceledError()
            code = self.active_process.wait()
            if code != 0:
                raise RuntimeError(f"Command failed with exit code {code}.")
            self._set_status("Command finished successfully.")
            self.root.after(0, lambda: messagebox.showinfo("Done", "Command finished successfully."))
        except CanceledError:
            self._set_status("Canceled.")
        except Exception as exc:
            self._set_status("Something went wrong.")
            self._log(f"Error: {exc}")
            self.root.after(0, lambda message=str(exc): messagebox.showerror("Error", message))
        finally:
            self.active_process = None
            self.root.after(0, self.finish)

    def check_setup_clicked(self) -> None:
        self._clear_log()
        ok, messages = self._check_setup(include_paths=True)
        for message in messages:
            self._log(message)
        self.setup_summary.set("Setup looks ready." if ok else "Setup needs attention. Check the Logs tab.")
        self.status_text.set("Setup looks ready." if ok else "Setup needs attention.")
        if ok:
            messagebox.showinfo("Setup check", "Setup looks ready.")
        else:
            messagebox.showwarning("Setup check", "Setup needs attention. See the Logs tab for details.")

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.input_file.get():
            messagebox.showerror("Missing audio", "Step 1: choose an audio file first.")
            return
        if not self.output_folder.get():
            messagebox.showerror("Missing output", "Step 2: choose an output folder first.")
            return
        if not any([self.export_vocals.get(), self.export_instrumental.get(), self.export_speaker_clips.get(), self.export_combined_speakers.get(), self.export_summary.get()]):
            messagebox.showerror("Missing output option", "Step 3: choose at least one output option.")
            return
        try:
            self._read_processing_options()
        except ValueError as exc:
            messagebox.showerror("Invalid processing option", str(exc))
            return
        ok, messages = self._check_setup(include_paths=False)
        self._clear_log()
        for message in messages:
            self._log(message)
        if not ok:
            self.status_text.set("Setup needs attention.")
            messagebox.showwarning("Setup check", "Setup needs attention. See the Logs tab or Setup & Settings tab.")
            return
        self.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.open_output_button.configure(state="disabled")
        self.progress.start(10)
        self.status_text.set("Processing audio...")
        self._save_settings()
        self.worker = threading.Thread(target=self.process, daemon=True)
        self.worker.start()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.status_text.set("Canceling...")
        self._log("Cancel requested.")
        if self.active_process and self.active_process.poll() is None:
            self.active_process.terminate()

    def process(self) -> None:
        try:
            src = Path(self.input_file.get())
            out = self._resolve_run_output_folder(src)
            self.last_output_path = out
            out.mkdir(parents=True, exist_ok=True)
            options = self._read_processing_options()
            self._log(f"Input: {src}")
            self._log(f"Output: {out}")
            stems_dir = out / "stems"
            self._set_status("Step 1/3: separating clean vocals with Demucs...")
            self._run_demucs(src, stems_dir)
            self._raise_if_canceled()
            vocals = stems_dir / DEMUCS_MODEL / src.stem / "vocals.wav"
            instrumental = stems_dir / DEMUCS_MODEL / src.stem / "no_vocals.wav"
            if not vocals.exists() or not instrumental.exists():
                raise RuntimeError(f"Demucs finished, but expected files were not found. Expected: {vocals} and {instrumental}")
            exports_dir = out / "exports"
            exports_dir.mkdir(exist_ok=True)
            if self.export_vocals.get():
                target = exports_dir / f"{src.stem}_vocals.wav"
                shutil.copy2(vocals, target)
                self._log(f"Saved clean vocals: {target}")
            if self.export_instrumental.get():
                target = exports_dir / f"{src.stem}_background.wav"
                shutil.copy2(instrumental, target)
                self._log(f"Saved background/no-vocals track: {target}")
            needs_speakers = self.export_speaker_clips.get() or self.export_combined_speakers.get() or self.export_summary.get()
            if needs_speakers:
                self._set_status("Step 2/3: detecting speakers...")
                self._create_speaker_outputs(src, vocals, out, options)
            else:
                self._log("Speaker detection skipped because no speaker outputs were selected.")
            self._raise_if_canceled()
            self._set_status(f"Done. Saved files to: {out}")
            self.root.after(0, lambda output=out: messagebox.showinfo("Finished", f"All done.\n\nSaved files to:\n{output}"))
        except CanceledError:
            self._set_status("Canceled.")
            self._log("Processing canceled.")
        except Exception as exc:
            self._set_status("Something went wrong.")
            self._log(f"Error: {exc}")
            self.root.after(0, lambda message=str(exc): messagebox.showerror("Error", message))
        finally:
            self.root.after(0, self.finish)

    def _run_demucs(self, src: Path, stems_dir: Path) -> None:
        command = [sys.executable, "-m", "demucs.separate", "--two-stems", "vocals", "-n", DEMUCS_MODEL, "-o", str(stems_dir), str(src)]
        self._log("Running Demucs vocal separation...")
        self._log("Command: " + " ".join(command))
        self.active_process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        try:
            assert self.active_process.stdout is not None
            for line in self.active_process.stdout:
                if line.strip():
                    self._log(line.rstrip())
                if self.cancel_event.is_set():
                    self.active_process.terminate()
                    raise CanceledError()
            return_code = self.active_process.wait()
            if self.cancel_event.is_set():
                raise CanceledError()
            if return_code != 0:
                raise RuntimeError(f"Demucs failed with exit code {return_code}.")
        finally:
            self.active_process = None

    def _create_speaker_outputs(self, src: Path, vocals: Path, out: Path, options: dict[str, float | int]) -> None:
        self._raise_if_canceled()
        self._log("Loading pyannote speaker diarization model...")
        try:
            import torch
            from huggingface_hub import HfFolder
            from pydub import AudioSegment
            from pyannote.audio import Pipeline
        except Exception as exc:
            raise RuntimeError(f"Could not load audio packages. Try Install / Update Requirements. {exc}") from exc
        token = HfFolder.get_token()
        if not token:
            raise RuntimeError("No Hugging Face login detected. Use Setup & Settings > Hugging Face Login.")
        try:
            pipeline = Pipeline.from_pretrained(PYANNOTE_MODEL, use_auth_token=token)
            if torch.cuda.is_available():
                pipeline.to(torch.device("cuda"))
                self._log("Using CUDA acceleration for pyannote.")
            else:
                self._log("CUDA not available. Using CPU.")
        except Exception as exc:
            raise RuntimeError(f"Could not load {PYANNOTE_MODEL}. Accept access on Hugging Face, then login again.") from exc
        diarization = pipeline(str(vocals))
        self._raise_if_canceled()
        self._set_status("Step 3/3: exporting speaker files...")
        audio = AudioSegment.from_file(vocals)
        segments_dir = out / "speaker_segments"
        combined_dir = out / "combined_speakers"
        segments_dir.mkdir(exist_ok=True)
        if self.export_combined_speakers.get():
            combined_dir.mkdir(exist_ok=True)
        counts: dict[str, int] = {}
        combined_audio: dict[str, AudioSegment] = {}
        rows: list[dict[str, str]] = []
        skipped_short = 0
        silence = AudioSegment.silent(duration=int(options["combined_gap_ms"]))
        for segment, _, speaker in diarization.itertracks(yield_label=True):
            self._raise_if_canceled()
            raw_duration = segment.end - segment.start
            if raw_duration < float(options["min_clip_seconds"]):
                skipped_short += 1
                continue
            counts[speaker] = counts.get(speaker, 0) + 1
            clip_number = counts[speaker]
            padding_ms = int(options["clip_padding_ms"])
            start_ms = max(0, int(segment.start * 1000) - padding_ms)
            end_ms = min(len(audio), int(segment.end * 1000) + padding_ms)
            clip = audio[start_ms:end_ms].normalize().strip_silence(silence_len=200, silence_thresh=-40)
            clip_path = ""
            if self.export_speaker_clips.get():
                speaker_dir = segments_dir / speaker
                speaker_dir.mkdir(exist_ok=True)
                target = speaker_dir / f"{src.stem}_{speaker}_{clip_number:03d}.wav"
                clip.export(target, format="wav")
                clip_path = str(target)
            if self.export_combined_speakers.get():
                current = combined_audio.get(speaker)
                combined_audio[speaker] = clip if current is None else current + silence + clip
            rows.append({
                "speaker": speaker,
                "clip_number": str(clip_number),
                "start_seconds": f"{segment.start:.2f}",
                "end_seconds": f"{segment.end:.2f}",
                "duration_seconds": f"{raw_duration:.2f}",
                "export_start_seconds": f"{start_ms / 1000:.2f}",
                "export_end_seconds": f"{end_ms / 1000:.2f}",
                "file": clip_path,
            })
        for speaker, speaker_audio in combined_audio.items():
            target = combined_dir / f"{src.stem}_{speaker}_combined.wav"
            speaker_audio.export(target, format="wav")
            self._log(f"Saved combined speaker file: {target}")
        if self.export_summary.get():
            self._write_summary(out, rows, skipped_short)
        self._log(f"Speaker export complete. Speakers: {len(counts)}. Clips: {sum(counts.values())}. Skipped short clips: {skipped_short}.")

    def _write_summary(self, out: Path, rows: list[dict[str, str]], skipped_short: int) -> None:
        csv_path = out / "speaker_segments_summary.csv"
        txt_path = out / "speaker_segments_summary.txt"
        fieldnames = ["speaker", "clip_number", "start_seconds", "end_seconds", "duration_seconds", "export_start_seconds", "export_end_seconds", "file"]
        with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        with txt_path.open("w", encoding="utf-8") as txt_file:
            if not rows:
                txt_file.write("No speaker segments were detected.\n")
            txt_file.write(f"Skipped short clips: {skipped_short}\n\n")
            for row in rows:
                txt_file.write("{speaker} clip {clip_number}: {start_seconds}s - {end_seconds}s ({duration_seconds}s), exported {export_start_seconds}s - {export_end_seconds}s {file}\n".format(**row))
        self._log(f"Saved summary CSV: {csv_path}")
        self._log(f"Saved summary TXT: {txt_path}")

    def _resolve_run_output_folder(self, src: Path) -> Path:
        base = Path(self.output_folder.get())
        if not self.create_job_folder.get():
            return base
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_stem = "".join(char if char.isalnum() or char in "-_ " else "_" for char in src.stem).strip() or "audio"
        return base / f"{safe_stem}_{timestamp}"

    def _read_processing_options(self) -> dict[str, float | int]:
        try:
            min_clip_seconds = float(self.min_clip_seconds.get())
            clip_padding_ms = int(self.clip_padding_ms.get())
            combined_gap_ms = int(self.combined_gap_ms.get())
        except ValueError as exc:
            raise ValueError("Minimum clip, padding, and gap must be numbers.") from exc
        if min_clip_seconds < 0 or clip_padding_ms < 0 or combined_gap_ms < 0:
            raise ValueError("Minimum clip, padding, and gap cannot be negative.")
        return {"min_clip_seconds": min_clip_seconds, "clip_padding_ms": clip_padding_ms, "combined_gap_ms": combined_gap_ms}

    def _load_settings(self) -> None:
        if not SETTINGS_PATH.exists():
            return
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        self.input_file.set(data.get("input_file", ""))
        self.output_folder.set(data.get("output_folder", ""))
        self.export_vocals.set(bool(data.get("export_vocals", True)))
        self.export_instrumental.set(bool(data.get("export_instrumental", True)))
        self.export_speaker_clips.set(bool(data.get("export_speaker_clips", True)))
        self.export_combined_speakers.set(bool(data.get("export_combined_speakers", True)))
        self.export_summary.set(bool(data.get("export_summary", True)))
        self.create_job_folder.set(bool(data.get("create_job_folder", True)))
        self.min_clip_seconds.set(str(data.get("min_clip_seconds", "0.30")))
        self.clip_padding_ms.set(str(data.get("clip_padding_ms", "150")))
        self.combined_gap_ms.set(str(data.get("combined_gap_ms", "500")))

    def _save_settings(self) -> None:
        data = {
            "input_file": self.input_file.get(),
            "output_folder": self.output_folder.get(),
            "export_vocals": self.export_vocals.get(),
            "export_instrumental": self.export_instrumental.get(),
            "export_speaker_clips": self.export_speaker_clips.get(),
            "export_combined_speakers": self.export_combined_speakers.get(),
            "export_summary": self.export_summary.get(),
            "create_job_folder": self.create_job_folder.get(),
            "min_clip_seconds": self.min_clip_seconds.get(),
            "clip_padding_ms": self.clip_padding_ms.get(),
            "combined_gap_ms": self.combined_gap_ms.get(),
        }
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            self._log(f"Could not save settings: {exc}")

    def _check_setup(self, include_paths: bool) -> tuple[bool, list[str]]:
        messages: list[str] = []
        ok = True
        messages.append(f"Python: {Path(sys.executable)}")
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            messages.append(f"FFmpeg: {ffmpeg}" if include_paths else "FFmpeg: found")
        else:
            ok = False
            messages.append("FFmpeg: missing. Install Gyan FFmpeg and add it to PATH.")
        packages = {"demucs": "demucs", "diffq": "diffq", "soundfile": "soundfile", "pydub": "pydub", "pyannote.audio": "pyannote.audio", "huggingface_hub": "huggingface_hub"}
        for package_name, import_name in packages.items():
            try:
                found = importlib.util.find_spec(import_name) is not None
            except (ImportError, ModuleNotFoundError, ValueError):
                found = False
            if found:
                messages.append(f"{package_name}: installed")
            else:
                ok = False
                messages.append(f"{package_name}: missing. Use Install / Update Requirements.")
        try:
            from huggingface_hub import HfFolder
            token = HfFolder.get_token()
        except Exception:
            token = None
        if token:
            messages.append("Hugging Face login: token found")
            messages.append(f"Pyannote access: checked when {PYANNOTE_MODEL} loads")
        else:
            ok = False
            messages.append("Hugging Face login: missing. Use Hugging Face Login.")
        return ok, messages

    def _run_startup_check(self) -> None:
        ok, messages = self._check_setup(include_paths=False)
        self._log("Startup setup check:")
        for message in messages:
            self._log(message)
        self.setup_summary.set("Setup looks ready." if ok else "Setup needs attention. Open Setup & Settings.")
        self.status_text.set("Setup looks ready." if ok else "Setup needs attention.")

    def _raise_if_canceled(self) -> None:
        if self.cancel_event.is_set():
            raise CanceledError()

    def _set_status(self, message: str) -> None:
        self.root.after(0, lambda value=message: self.status_text.set(value))
        self._log(message)

    def _log(self, message: str) -> None:
        self.log_queue.put(f"[{time.strftime('%H:%M:%S')}] {message}")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _drain_log_queue(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(100, self._drain_log_queue)

    def on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Quit", "Processing is still running. Cancel and close?"):
                return
            self.cancel()
        self._save_settings()
        self.root.destroy()

    def finish(self) -> None:
        self.progress.stop()
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        if self.last_output_path and self.last_output_path.exists():
            self.open_output_button.configure(state="normal")


class CanceledError(Exception):
    pass


if __name__ == "__main__":
    app_root = tk.Tk()
    SimpleVoiceSplitterApp(app_root)
    app_root.mainloop()
