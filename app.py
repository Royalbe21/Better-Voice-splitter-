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
SETTINGS_PATH = Path(os.environ.get("APPDATA", Path.home())) / APP_TITLE / "settings.json"


class SimpleVoiceSplitterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("860x640")
        self.root.minsize(780, 560)

        self.input_file = tk.StringVar()
        self.output_folder = tk.StringVar()
        self.status_text = tk.StringVar(value="Choose an audio file and output folder.")

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
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(5, weight=1)

        ttk.Label(outer, text=APP_TITLE, font=("Segoe UI", 18, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            outer,
            text="Separate vocals, create instrumental tracks, and split vocals into speaker clips.",
            foreground="#555",
        ).grid(row=1, column=0, sticky="w", pady=(0, 14))

        picker = ttk.Frame(outer)
        picker.grid(row=2, column=0, sticky="ew")
        picker.columnconfigure(1, weight=1)

        ttk.Button(picker, text="Choose Audio", command=self.pick_file).grid(
            row=0, column=0, sticky="ew", padx=(0, 10), pady=4
        )
        ttk.Label(picker, textvariable=self.input_file, foreground="#333").grid(
            row=0, column=1, sticky="ew", pady=4
        )

        ttk.Button(picker, text="Choose Output", command=self.pick_output).grid(
            row=1, column=0, sticky="ew", padx=(0, 10), pady=4
        )
        ttk.Label(picker, textvariable=self.output_folder, foreground="#333").grid(
            row=1, column=1, sticky="ew", pady=4
        )

        options = ttk.LabelFrame(outer, text="Output options", padding=12)
        options.grid(row=3, column=0, sticky="ew", pady=(12, 12))
        for column in range(3):
            options.columnconfigure(column, weight=1)

        ttk.Checkbutton(options, text="Vocals WAV", variable=self.export_vocals).grid(
            row=0, column=0, sticky="w", padx=(0, 16), pady=3
        )
        ttk.Checkbutton(
            options, text="Instrumental WAV", variable=self.export_instrumental
        ).grid(row=0, column=1, sticky="w", padx=(0, 16), pady=3)
        ttk.Checkbutton(
            options,
            text="Individual speaker clips",
            variable=self.export_speaker_clips,
        ).grid(row=0, column=2, sticky="w", pady=3)
        ttk.Checkbutton(
            options,
            text="Combined file per speaker",
            variable=self.export_combined_speakers,
        ).grid(row=1, column=0, sticky="w", padx=(0, 16), pady=3)
        ttk.Checkbutton(
            options,
            text="CSV and TXT summary",
            variable=self.export_summary,
        ).grid(row=1, column=1, sticky="w", padx=(0, 16), pady=3)
        ttk.Checkbutton(
            options,
            text="New folder for each run",
            variable=self.create_job_folder,
        ).grid(row=1, column=2, sticky="w", pady=3)

        processing = ttk.LabelFrame(outer, text="Processing", padding=12)
        processing.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        for column in range(6):
            processing.columnconfigure(column, weight=1)

        ttk.Label(processing, text="Minimum clip seconds").grid(row=0, column=0, sticky="w")
        ttk.Entry(processing, textvariable=self.min_clip_seconds, width=10).grid(
            row=0, column=1, sticky="w", padx=(8, 20)
        )
        ttk.Label(processing, text="Clip padding ms").grid(row=0, column=2, sticky="w")
        ttk.Entry(processing, textvariable=self.clip_padding_ms, width=10).grid(
            row=0, column=3, sticky="w", padx=(8, 20)
        )
        ttk.Label(processing, text="Combined gap ms").grid(row=0, column=4, sticky="w")
        ttk.Entry(processing, textvariable=self.combined_gap_ms, width=10).grid(
            row=0, column=5, sticky="w", padx=(8, 0)
        )

        log_frame = ttk.Frame(outer)
        log_frame.grid(row=5, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame,
            height=14,
            wrap="word",
            state="disabled",
            background="#f7f7f7",
            foreground="#222",
            relief="solid",
            borderwidth=1,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        controls = ttk.Frame(outer)
        controls.grid(row=6, column=0, sticky="ew", pady=(12, 0))
        controls.columnconfigure(4, weight=1)

        self.check_button = ttk.Button(controls, text="Check Setup", command=self.check_setup_clicked)
        self.check_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.start_button = ttk.Button(controls, text="Start", command=self.start)
        self.start_button.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        self.cancel_button = ttk.Button(
            controls, text="Cancel", command=self.cancel, state="disabled"
        )
        self.cancel_button.grid(row=0, column=2, sticky="ew", padx=(0, 8))

        self.open_output_button = ttk.Button(
            controls, text="Open Output", command=self.open_last_output, state="disabled"
        )
        self.open_output_button.grid(row=0, column=3, sticky="ew", padx=(0, 8))

        ttk.Label(controls, textvariable=self.status_text, foreground="#1f4e79").grid(
            row=0, column=4, sticky="w"
        )

        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.grid(row=7, column=0, sticky="ew", pady=(10, 0))

    def pick_file(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Choose an audio file",
            filetypes=[("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg"), ("All files", "*.*")],
        )
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

    def check_setup_clicked(self) -> None:
        self._clear_log()
        ok, messages = self._check_setup(include_paths=True)
        for message in messages:
            self._log(message)
        if ok:
            self.status_text.set("Setup looks ready.")
            messagebox.showinfo("Setup check", "Setup looks ready.")
        else:
            self.status_text.set("Setup needs attention.")
            messagebox.showwarning("Setup check", "Setup needs attention. See the log for details.")

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        if not self.input_file.get():
            messagebox.showerror("Missing audio", "Please choose an audio file.")
            return
        if not self.output_folder.get():
            messagebox.showerror("Missing output", "Please choose an output folder.")
            return
        if not any(
            [
                self.export_vocals.get(),
                self.export_instrumental.get(),
                self.export_speaker_clips.get(),
                self.export_combined_speakers.get(),
                self.export_summary.get(),
            ]
        ):
            messagebox.showerror("Missing output option", "Please choose at least one output option.")
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
            messagebox.showwarning("Setup check", "Setup needs attention. See the log for details.")
            return

        self.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.check_button.configure(state="disabled")
        self.open_output_button.configure(state="disabled")
        self.progress.start(10)
        self.status_text.set("Processing...")
        self._save_settings()

        self.worker = threading.Thread(target=self.process, daemon=True)
        self.worker.start()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.status_text.set("Canceling...")
        self._log("Cancel requested.")
        process = self.active_process
        if process and process.poll() is None:
            self._log("Stopping the active command...")
            process.terminate()

    def process(self) -> None:
        try:
            src = Path(self.input_file.get())
            out = self._resolve_run_output_folder(src)
            self.last_output_path = out
            out.mkdir(parents=True, exist_ok=True)
            options = self._read_processing_options()

            self._log(f"Input: {src}")
            self._log(f"Output: {out}")
            self._log(
                "Options: minimum clip "
                f"{options['min_clip_seconds']:.2f}s, padding {options['clip_padding_ms']}ms, "
                f"combined gap {options['combined_gap_ms']}ms"
            )

            stems_dir = out / "stems"
            self._set_status("Separating vocals with Demucs...")
            self._run_demucs(src, stems_dir)
            self._raise_if_canceled()

            vocals = stems_dir / "htdemucs" / src.stem / "vocals.wav"
            instrumental = stems_dir / "htdemucs" / src.stem / "no_vocals.wav"
            if not vocals.exists() or not instrumental.exists():
                raise RuntimeError("Demucs finished, but the expected vocal files were not found.")

            exports_dir = out / "exports"
            exports_dir.mkdir(exist_ok=True)
            if self.export_vocals.get():
                target = exports_dir / f"{src.stem}_vocals.wav"
                shutil.copy2(vocals, target)
                self._log(f"Saved vocals: {target}")
            if self.export_instrumental.get():
                target = exports_dir / f"{src.stem}_instrumental.wav"
                shutil.copy2(instrumental, target)
                self._log(f"Saved instrumental: {target}")

            needs_speakers = (
                self.export_speaker_clips.get()
                or self.export_combined_speakers.get()
                or self.export_summary.get()
            )
            if needs_speakers:
                self._create_speaker_outputs(src, vocals, out, options)
            else:
                self._log("Speaker detection skipped because no speaker outputs were selected.")

            self._raise_if_canceled()
            self._set_status(f"Done. Saved files to: {out}")
            self.root.after(
                0,
                lambda output=out: messagebox.showinfo(
                    "Finished", f"All done.\n\nSaved files to:\n{output}"
                ),
            )
        except CanceledError:
            self._set_status("Canceled.")
            self._log("Processing canceled.")
        except Exception as exc:
            error = str(exc)
            self._set_status("Something went wrong.")
            self._log(f"Error: {error}")
            self.root.after(0, lambda message=error: messagebox.showerror("Error", message))
        finally:
            self.root.after(0, self.finish)

    def _run_demucs(self, src: Path, stems_dir: Path) -> None:
       command = [
    sys.executable,
    "-m",
    "demucs.separate",
    "--two-stems",
    "vocals",
    "-n",
    "htdemucs_ft",
    "-o",
    str(stems_dir),
    str(src),
]
        self._log("Running Demucs vocal separation...")

        self.active_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
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

    def _create_speaker_outputs(
        self, src: Path, vocals: Path, out: Path, options: dict[str, float | int]
    ) -> None:
        self._raise_if_canceled()
        self._set_status("Loading speaker detection model...")
        self._log("Loading pyannote speaker diarization model...")

        try:
            from huggingface_hub import HfFolder
            from pydub import AudioSegment
            from pyannote.audio import Pipeline
        except Exception as exc:
            raise RuntimeError(f"Could not load audio packages. Try reinstalling requirements. {exc}") from exc

        token = HfFolder.get_token()
        if not token:
            raise RuntimeError("No Hugging Face login detected. Run: huggingface-cli login")

        try:
            pipeline = Pipeline.from_pretrained(PYANNOTE_MODEL, use_auth_token=token)
import torch

if torch.cuda.is_available():
    pipeline.to(torch.device("cuda"))
    self._log("Using CUDA acceleration for pyannote.")
else:
    self._log("CUDA not available. Using CPU.")
        except Exception as exc:
            raise RuntimeError(
                "Could not load the pyannote speaker model. Make sure your Hugging Face "
                f"account has accepted access for {PYANNOTE_MODEL}."
            ) from exc

        self._raise_if_canceled()
        self._set_status("Detecting speakers...")
        diarization = pipeline(str(vocals))

        self._raise_if_canceled()
        self._set_status("Exporting speaker files...")
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
            clip = audio[start_ms:end_ms]

# normalize volume
clip = clip.normalize()

# trim silence
clip = clip.strip_silence(
    silence_len=200,
    silence_thresh=-40
)

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

            rows.append(
                {
                    "speaker": speaker,
                    "clip_number": str(clip_number),
                    "start_seconds": f"{segment.start:.2f}",
                    "end_seconds": f"{segment.end:.2f}",
                    "duration_seconds": f"{raw_duration:.2f}",
                    "export_start_seconds": f"{start_ms / 1000:.2f}",
                    "export_end_seconds": f"{end_ms / 1000:.2f}",
                    "file": clip_path,
                }
            )

        for speaker, speaker_audio in combined_audio.items():
            self._raise_if_canceled()
            target = combined_dir / f"{src.stem}_{speaker}_combined.wav"
            speaker_audio.export(target, format="wav")
            self._log(f"Saved combined speaker file: {target}")

        if self.export_summary.get():
            self._write_summary(out, rows, skipped_short)

        total_clips = sum(counts.values())
        self._log(
            f"Speaker export complete. Speakers: {len(counts)}. Clips: {total_clips}. "
            f"Skipped short clips: {skipped_short}."
        )

    def _write_summary(self, out: Path, rows: list[dict[str, str]], skipped_short: int) -> None:
        csv_path = out / "speaker_segments_summary.csv"
        txt_path = out / "speaker_segments_summary.txt"
        fieldnames = [
            "speaker",
            "clip_number",
            "start_seconds",
            "end_seconds",
            "duration_seconds",
            "export_start_seconds",
            "export_end_seconds",
            "file",
        ]

        with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        with txt_path.open("w", encoding="utf-8") as txt_file:
            if not rows:
                txt_file.write("No speaker segments were detected.\n")
            txt_file.write(f"Skipped short clips: {skipped_short}\n\n")
            for row in rows:
                txt_file.write(
                    "{speaker} clip {clip_number}: {start_seconds}s - {end_seconds}s "
                    "({duration_seconds}s), exported {export_start_seconds}s - "
                    "{export_end_seconds}s {file}\n".format(**row)
                )

        self._log(f"Saved summary CSV: {csv_path}")
        self._log(f"Saved summary TXT: {txt_path}")

    def _resolve_run_output_folder(self, src: Path) -> Path:
        base = Path(self.output_folder.get())
        if not self.create_job_folder.get():
            return base

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_stem = "".join(char if char.isalnum() or char in "-_ " else "_" for char in src.stem).strip()
        safe_stem = safe_stem or "audio"
        return base / f"{safe_stem}_{timestamp}"

    def _read_processing_options(self) -> dict[str, float | int]:
        try:
            min_clip_seconds = float(self.min_clip_seconds.get())
            clip_padding_ms = int(self.clip_padding_ms.get())
            combined_gap_ms = int(self.combined_gap_ms.get())
        except ValueError as exc:
            raise ValueError("Minimum clip, padding, and gap must be numbers.") from exc

        if min_clip_seconds < 0:
            raise ValueError("Minimum clip seconds cannot be negative.")
        if clip_padding_ms < 0:
            raise ValueError("Clip padding cannot be negative.")
        if combined_gap_ms < 0:
            raise ValueError("Combined gap cannot be negative.")

        return {
            "min_clip_seconds": min_clip_seconds,
            "clip_padding_ms": clip_padding_ms,
            "combined_gap_ms": combined_gap_ms,
        }

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
        self.min_clip_seconds.set(str(data.get("min_clip_seconds", "0.75")))
        self.clip_padding_ms.set(str(data.get("clip_padding_ms", "250")))
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

        python_path = Path(sys.executable)
        messages.append(f"Python: {python_path}")

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            messages.append(f"FFmpeg: {ffmpeg}" if include_paths else "FFmpeg: found")
        else:
            ok = False
            messages.append("FFmpeg: missing. Install it with: winget install --id Gyan.FFmpeg -e")

        packages = {
            "demucs": "demucs",
            "pydub": "pydub",
            "pyannote.audio": "pyannote.audio",
            "huggingface_hub": "huggingface_hub",
        }
        for package_name, import_name in packages.items():
            try:
                found = importlib.util.find_spec(import_name) is not None
            except (ImportError, ModuleNotFoundError, ValueError):
                found = False

            if found:
                messages.append(f"{package_name}: installed")
            else:
                ok = False
                messages.append(f"{package_name}: missing. Run: {sys.executable} -m pip install -r requirements.txt")

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
            messages.append("Hugging Face login: missing. Run: huggingface-cli login")

        return ok, messages

    def _run_startup_check(self) -> None:
        ok, messages = self._check_setup(include_paths=False)
        self._log("Startup setup check:")
        for message in messages:
            self._log(message)
        self.status_text.set("Setup looks ready." if ok else "Setup needs attention.")

    def _raise_if_canceled(self) -> None:
        if self.cancel_event.is_set():
            raise CanceledError()

    def _set_status(self, message: str) -> None:
        self.root.after(0, lambda value=message: self.status_text.set(value))
        self._log(message)

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{timestamp}] {message}")

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
        self.check_button.configure(state="normal")
        if self.last_output_path and self.last_output_path.exists():
            self.open_output_button.configure(state="normal")


class CanceledError(Exception):
    pass


if __name__ == "__main__":
    app_root = tk.Tk()
    SimpleVoiceSplitterApp(app_root)
    app_root.mainloop()
