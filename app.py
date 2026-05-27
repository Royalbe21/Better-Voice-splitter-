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
import wave
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


APP_TITLE = "Easy Voice Splitter"
PYANNOTE_MODEL = "pyannote/speaker-diarization-3.1"
DEMUCS_MODEL = "mdx_extra_q"
SETTINGS_PATH = Path(os.environ.get("APPDATA", Path.home())) / APP_TITLE / "settings.json"
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}

PRESETS = {
    "Podcast / Interview": {
        "description": "Balanced quality for normal conversations, interviews, podcasts, and meetings.",
        "min_clip_seconds": "0.30",
        "clip_padding_ms": "150",
        "combined_gap_ms": "500",
        "speaker_clips": True,
        "combined": True,
        "summary": True,
    },
    "Phone Call": {
        "description": "More padding for clipped phone audio and compressed recordings.",
        "min_clip_seconds": "0.25",
        "clip_padding_ms": "250",
        "combined_gap_ms": "650",
        "speaker_clips": True,
        "combined": True,
        "summary": True,
    },
    "Noisy Recording": {
        "description": "Skips tiny fragments and adds extra padding so speech is less chopped.",
        "min_clip_seconds": "0.60",
        "clip_padding_ms": "300",
        "combined_gap_ms": "700",
        "speaker_clips": True,
        "combined": True,
        "summary": True,
    },
    "Fast Preview": {
        "description": "Creates main files and report with fewer small clips. Good for quick testing.",
        "min_clip_seconds": "1.00",
        "clip_padding_ms": "100",
        "combined_gap_ms": "400",
        "speaker_clips": False,
        "combined": True,
        "summary": True,
    },
    "Maximum Detail": {
        "description": "Exports smaller speaker fragments and detailed reports. Slower, but thorough.",
        "min_clip_seconds": "0.15",
        "clip_padding_ms": "200",
        "combined_gap_ms": "500",
        "speaker_clips": True,
        "combined": True,
        "summary": True,
    },
}


class SimpleVoiceSplitterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1180x780")
        self.root.minsize(1040, 680)

        self.audio_queue: list[Path] = []
        self.result_paths: list[Path] = []
        self.cancel_event = threading.Event()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.active_process: subprocess.Popen[str] | None = None
        self.worker: threading.Thread | None = None
        self.last_output_path: Path | None = None

        self.input_file = tk.StringVar()
        self.output_folder = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.status_text = tk.StringVar(value="Ready")
        self.setup_summary = tk.StringVar(value="Run setup check before your first job.")
        self.preset_name = tk.StringVar(value="Podcast / Interview")
        self.preset_description = tk.StringVar(value=PRESETS["Podcast / Interview"]["description"])

        self.export_vocals = tk.BooleanVar(value=True)
        self.export_instrumental = tk.BooleanVar(value=True)
        self.export_speaker_clips = tk.BooleanVar(value=True)
        self.export_combined_speakers = tk.BooleanVar(value=True)
        self.export_summary = tk.BooleanVar(value=True)
        self.create_job_folder = tk.BooleanVar(value=True)
        self.min_clip_seconds = tk.StringVar(value="0.30")
        self.clip_padding_ms = tk.StringVar(value="150")
        self.combined_gap_ms = tk.StringVar(value="500")

        self.stage_labels: dict[str, ttk.Label] = {}

        self._apply_theme()
        self._build_ui()
        self._load_settings()
        self._apply_preset(update_options=False)
        self.root.after(100, self._drain_log_queue)
        self.root.after(250, self._run_startup_check)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ----------------------------- UI -----------------------------
    def _apply_theme(self) -> None:
        self.colors = {
            "bg": "#111827",
            "panel": "#1f2937",
            "panel2": "#273449",
            "text": "#f9fafb",
            "muted": "#cbd5e1",
            "accent": "#38bdf8",
            "success": "#22c55e",
            "warn": "#f59e0b",
            "danger": "#ef4444",
        }
        self.root.configure(bg=self.colors["bg"])
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("Card.TFrame", background=self.colors["panel"], relief="flat")
        style.configure("TLabel", background=self.colors["bg"], foreground=self.colors["text"], font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=self.colors["bg"], foreground=self.colors["muted"])
        style.configure("Card.TLabel", background=self.colors["panel"], foreground=self.colors["text"])
        style.configure("CardMuted.TLabel", background=self.colors["panel"], foreground=self.colors["muted"])
        style.configure("Title.TLabel", background=self.colors["bg"], foreground=self.colors["text"], font=("Segoe UI", 22, "bold"))
        style.configure("Hero.TLabel", background=self.colors["panel"], foreground=self.colors["text"], font=("Segoe UI", 15, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=8)
        style.configure("TButton", padding=7)
        style.configure("TCheckbutton", background=self.colors["panel"], foreground=self.colors["text"], padding=3)
        style.configure("TLabelframe", background=self.colors["bg"], foreground=self.colors["text"], bordercolor=self.colors["panel2"])
        style.configure("TLabelframe.Label", background=self.colors["bg"], foreground=self.colors["accent"], font=("Segoe UI", 10, "bold"))
        style.configure("TNotebook", background=self.colors["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 8), font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", background="#0f172a", fieldbackground="#0f172a", foreground=self.colors["text"], rowheight=26)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="🎙️ Easy Voice Splitter", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="AI-powered vocal cleanup, speaker splitting, reports, and batch processing for Windows.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w")
        self.status_badge = tk.Label(
            header,
            textvariable=self.status_text,
            bg="#082f49",
            fg="#e0f2fe",
            padx=14,
            pady=7,
            font=("Segoe UI", 10, "bold"),
        )
        self.status_badge.grid(row=0, column=1, rowspan=2, sticky="e")

        notebook = ttk.Notebook(outer)
        notebook.grid(row=1, column=0, sticky="nsew")
        self.run_tab = ttk.Frame(notebook, padding=14)
        self.results_tab = ttk.Frame(notebook, padding=14)
        self.setup_tab = ttk.Frame(notebook, padding=14)
        self.logs_tab = ttk.Frame(notebook, padding=14)
        notebook.add(self.run_tab, text="Run Job")
        notebook.add(self.results_tab, text="Results")
        notebook.add(self.setup_tab, text="Setup & Repair")
        notebook.add(self.logs_tab, text="Logs")

        self._build_run_tab()
        self._build_results_tab()
        self._build_setup_tab()
        self._build_logs_tab()

    def _build_run_tab(self) -> None:
        tab = self.run_tab
        tab.columnconfigure(0, weight=3)
        tab.columnconfigure(1, weight=2)
        tab.rowconfigure(1, weight=1)

        left = ttk.Frame(tab)
        left.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=(0, 12))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        self._build_queue_card(left)
        self._build_output_card(left)
        self._build_action_card(left)

        right = ttk.Frame(tab)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        self._build_preset_card(right)
        self._build_options_card(right)
        self._build_stage_card(right)
        self._build_waveform_card(right)

    def _card(self, parent: ttk.Frame, title: str, row: int, column: int = 0, **grid_kwargs) -> ttk.LabelFrame:
        card = ttk.LabelFrame(parent, text=title, padding=12)
        defaults = {"sticky": "nsew", "pady": (0, 12)}
        defaults.update(grid_kwargs)
        card.grid(row=row, column=column, **defaults)
        return card

    def _build_queue_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "1 — Add recordings", 0)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)
        ttk.Label(
            card,
            text="Add one file or a folder full of audio. The app will process the queue one recording at a time.",
            style="Muted.TLabel",
        ).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 8))
        ttk.Button(card, text="Add Audio Files", command=self.add_audio_files).grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(card, text="Add Folder", command=self.add_audio_folder).grid(row=1, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(card, text="Remove Selected", command=self.remove_selected_audio).grid(row=1, column=2, sticky="ew", padx=(0, 8))
        ttk.Button(card, text="Clear Queue", command=self.clear_queue).grid(row=1, column=3, sticky="ew")
        ttk.Button(card, text="Preview File", command=self.preview_selected_audio).grid(row=1, column=4, sticky="ew", padx=(8, 0))

        self.queue_list = tk.Listbox(
            card,
            height=8,
            bg="#0f172a",
            fg=self.colors["text"],
            selectbackground="#0369a1",
            selectforeground="#ffffff",
            relief="flat",
            font=("Segoe UI", 10),
        )
        self.queue_list.grid(row=2, column=0, columnspan=5, sticky="nsew", pady=(10, 0))
        self.queue_list.bind("<<ListboxSelect>>", lambda _event: self.draw_waveform_for_selected())

    def _build_output_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "2 — Choose output location", 1)
        card.columnconfigure(1, weight=1)
        ttk.Button(card, text="Choose Output Folder", command=self.pick_output).grid(row=0, column=0, sticky="ew", padx=(0, 10))
        ttk.Label(card, textvariable=self.output_folder, style="Muted.TLabel").grid(row=0, column=1, sticky="ew")
        ttk.Checkbutton(card, text="Create a new timestamped folder for each recording", variable=self.create_job_folder).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_action_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "3 — Process", 2)
        for i in range(5):
            card.columnconfigure(i, weight=1)
        self.start_button = ttk.Button(card, text="🚀 Start Processing", command=self.start, style="Accent.TButton")
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.cancel_button = ttk.Button(card, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(card, text="Check Setup", command=self.check_setup_clicked).grid(row=0, column=2, sticky="ew", padx=(0, 8))
        ttk.Button(card, text="Open Last Output", command=self.open_last_output).grid(row=0, column=3, sticky="ew", padx=(0, 8))
        self.progress = ttk.Progressbar(card, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=4, sticky="ew")

    def _build_preset_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "Processing preset", 0)
        card.columnconfigure(0, weight=1)
        preset = ttk.Combobox(card, textvariable=self.preset_name, values=list(PRESETS.keys()), state="readonly")
        preset.grid(row=0, column=0, sticky="ew")
        preset.bind("<<ComboboxSelected>>", lambda _event: self._apply_preset(update_options=True))
        ttk.Label(card, textvariable=self.preset_description, style="Muted.TLabel", wraplength=420).grid(row=1, column=0, sticky="w", pady=(8, 0))

    def _build_options_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "Outputs to create", 1)
        card.columnconfigure(0, weight=1)
        options = [
            ("Clean vocals WAV", "Isolated voice track from Demucs.", self.export_vocals),
            ("Background/no-vocals WAV", "Useful for checking what was removed.", self.export_instrumental),
            ("Individual speaker clips", "Separate clips for every detected speaker segment.", self.export_speaker_clips),
            ("Combined file per speaker", "One longer file for each speaker.", self.export_combined_speakers),
            ("CSV/TXT speaker report", "Timestamps and speaker labels.", self.export_summary),
        ]
        for row, (title, desc, var) in enumerate(options):
            ttk.Checkbutton(card, text=title, variable=var).grid(row=row, column=0, sticky="w")
            ttk.Label(card, text=desc, style="Muted.TLabel", wraplength=420).grid(row=row, column=1, sticky="w", padx=(8, 0))

        settings = ttk.Frame(card)
        settings.grid(row=len(options), column=0, columnspan=2, sticky="ew", pady=(12, 0))
        for i in range(6):
            settings.columnconfigure(i, weight=1)
        ttk.Label(settings, text="Min clip sec").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.min_clip_seconds, width=8).grid(row=0, column=1, sticky="w")
        ttk.Label(settings, text="Padding ms").grid(row=0, column=2, sticky="w")
        ttk.Entry(settings, textvariable=self.clip_padding_ms, width=8).grid(row=0, column=3, sticky="w")
        ttk.Label(settings, text="Gap ms").grid(row=0, column=4, sticky="w")
        ttk.Entry(settings, textvariable=self.combined_gap_ms, width=8).grid(row=0, column=5, sticky="w")

    def _build_stage_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "Live progress", 2)
        for i, stage in enumerate(["Vocal cleanup", "Speaker detection", "Export files"]):
            label = ttk.Label(card, text=f"○ {stage}", style="Muted.TLabel")
            label.grid(row=i, column=0, sticky="w", pady=2)
            self.stage_labels[stage] = label

    def _build_waveform_card(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "Quick waveform preview", 3)
        card.columnconfigure(0, weight=1)
        self.waveform_canvas = tk.Canvas(card, height=110, bg="#0f172a", highlightthickness=0)
        self.waveform_canvas.grid(row=0, column=0, sticky="ew")
        ttk.Label(card, text="Select a WAV file in the queue to draw a simple waveform preview.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 0))

    def _build_results_tab(self) -> None:
        tab = self.results_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        top = ttk.Frame(tab)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(top, text="Open Selected", command=self.open_selected_result).pack(side="left", padx=(0, 8))
        ttk.Button(top, text="Open Containing Folder", command=self.open_selected_result_folder).pack(side="left", padx=(0, 8))
        ttk.Button(top, text="Rename Selected Speaker File", command=self.rename_selected_result).pack(side="left", padx=(0, 8))
        ttk.Button(top, text="Refresh Last Output", command=self.refresh_last_output_results).pack(side="left")

        self.results_tree = ttk.Treeview(tab, columns=("kind", "path"), show="headings")
        self.results_tree.heading("kind", text="Type")
        self.results_tree.heading("path", text="File")
        self.results_tree.column("kind", width=180, anchor="w")
        self.results_tree.column("path", width=820, anchor="w")
        self.results_tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=self.results_tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.results_tree.configure(yscrollcommand=scrollbar.set)

    def _build_setup_tab(self) -> None:
        tab = self.setup_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        tools = self._card(tab, "Setup and repair tools", 0)
        for i in range(5):
            tools.columnconfigure(i, weight=1)
        ttk.Button(tools, text="Check Setup", command=self.check_setup_clicked).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(tools, text="Install / Update Requirements", command=self.install_requirements).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(tools, text="Hugging Face Login", command=self.huggingface_login).grid(row=0, column=2, sticky="ew", padx=(0, 8))
        ttk.Button(tools, text="Open Project Folder", command=self.open_project_folder).grid(row=0, column=3, sticky="ew", padx=(0, 8))
        ttk.Button(tools, text="Copy Environment Report", command=self.copy_environment_report).grid(row=0, column=4, sticky="ew")
        ttk.Label(tools, textvariable=self.setup_summary, style="Muted.TLabel", wraplength=1000).grid(row=1, column=0, columnspan=5, sticky="w", pady=(10, 0))

        model_card = self._card(tab, "AI model configuration", 1)
        ttk.Label(model_card, text=f"Demucs model: {DEMUCS_MODEL}", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(model_card, text=f"Speaker model: {PYANNOTE_MODEL}", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(model_card, text=f"Settings file: {SETTINGS_PATH}", style="Muted.TLabel").pack(anchor="w")

        help_card = self._card(tab, "Recommended stable environment", 2)
        help_text = (
            "This app is sensitive to Python audio package versions. Use the pinned requirements in this repo.\n\n"
            "Known-good stack used during testing:\n"
            "• Python 3.11\n"
            "• torch 2.5.1 + CUDA 12.4\n"
            "• torchaudio 2.5.1 + CUDA 12.4\n"
            "• pyannote.audio 3.1.1\n"
            "• speechbrain 0.5.16\n"
            "• numpy < 2\n\n"
            "Avoid upgrading everything blindly. Newer audio packages can reintroduce TorchCodec, NumPy, or Hugging Face API conflicts."
        )
        ttk.Label(help_card, text=help_text, style="Muted.TLabel", justify="left", wraplength=1000).pack(anchor="w")

    def _build_logs_tab(self) -> None:
        tab = self.logs_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        self.log_text = tk.Text(tab, wrap="word", state="disabled", background="#020617", foreground="#e5e7eb", insertbackground="#e5e7eb", relief="flat", font=("Consolas", 10))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        ttk.Button(tab, text="Clear Logs", command=self._clear_log).grid(row=1, column=0, sticky="w", pady=(10, 0))

    # ----------------------------- Queue and files -----------------------------
    def add_audio_files(self) -> None:
        files = filedialog.askopenfilenames(title="Choose audio files", filetypes=[("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg"), ("All files", "*.*")])
        self._add_paths([Path(f) for f in files])

    def add_audio_folder(self) -> None:
        folder = filedialog.askdirectory(title="Choose folder containing audio")
        if not folder:
            return
        paths = [p for p in Path(folder).rglob("*") if p.suffix.lower() in AUDIO_EXTENSIONS]
        self._add_paths(paths)

    def _add_paths(self, paths: list[Path]) -> None:
        added = 0
        existing = {p.resolve() for p in self.audio_queue if p.exists()}
        for path in paths:
            if path.exists() and path.suffix.lower() in AUDIO_EXTENSIONS and path.resolve() not in existing:
                self.audio_queue.append(path)
                self.queue_list.insert("end", str(path))
                existing.add(path.resolve())
                added += 1
        if added:
            self.input_file.set(str(self.audio_queue[0]))
            self.status_text.set(f"Added {added} audio file(s).")
            self.draw_waveform_for_selected(first_if_none=True)

    def remove_selected_audio(self) -> None:
        selected = list(self.queue_list.curselection())
        selected.reverse()
        for index in selected:
            self.queue_list.delete(index)
            del self.audio_queue[index]
        if self.audio_queue:
            self.input_file.set(str(self.audio_queue[0]))
        else:
            self.input_file.set("")

    def clear_queue(self) -> None:
        self.audio_queue.clear()
        self.queue_list.delete(0, "end")
        self.input_file.set("")
        self.waveform_canvas.delete("all")

    def pick_output(self) -> None:
        folder = filedialog.askdirectory(title="Choose output folder")
        if folder:
            self.output_folder.set(folder)

    def preview_selected_audio(self) -> None:
        path = self._selected_queue_path()
        if path:
            os.startfile(path)

    def _selected_queue_path(self) -> Path | None:
        selected = self.queue_list.curselection()
        if selected:
            return self.audio_queue[selected[0]]
        if self.audio_queue:
            return self.audio_queue[0]
        return None

    def draw_waveform_for_selected(self, first_if_none: bool = False) -> None:
        path = self._selected_queue_path()
        if first_if_none and not path and self.audio_queue:
            path = self.audio_queue[0]
        self.waveform_canvas.delete("all")
        if not path:
            return
        if path.suffix.lower() != ".wav":
            self.waveform_canvas.create_text(12, 55, anchor="w", fill="#94a3b8", text="Waveform preview is available for WAV files. Other formats still process normally.")
            return
        try:
            with wave.open(str(path), "rb") as wav:
                frames = wav.getnframes()
                channels = wav.getnchannels()
                width = max(1, self.waveform_canvas.winfo_width() or 420)
                height = 110
                step = max(1, frames // width)
                samples = []
                for _ in range(width):
                    data = wav.readframes(step)
                    if not data:
                        break
                    if wav.getsampwidth() == 2:
                        vals = [int.from_bytes(data[i:i+2], "little", signed=True) for i in range(0, len(data) - 1, 2 * channels)]
                        amp = max([abs(v) for v in vals], default=0) / 32768
                    else:
                        amp = 0.2
                    samples.append(amp)
            mid = height // 2
            for x, amp in enumerate(samples):
                y = int(amp * (height // 2 - 8))
                self.waveform_canvas.create_line(x, mid - y, x, mid + y, fill="#38bdf8")
        except Exception as exc:
            self.waveform_canvas.create_text(12, 55, anchor="w", fill="#fca5a5", text=f"Could not draw waveform: {exc}")

    # ----------------------------- Setup -----------------------------
    def install_requirements(self) -> None:
        req = Path("requirements.txt")
        if not req.exists():
            messagebox.showerror("Missing requirements", "requirements.txt was not found in this folder.")
            return
        self._run_background_command([sys.executable, "-m", "pip", "install", "-r", str(req)], "Installing requirements...")

    def huggingface_login(self) -> None:
        candidates = [
            Path(sys.executable).parent / "hf.exe",
            Path(sys.executable).parent / "huggingface-cli.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                command = f'& "{candidate}" auth login --force' if candidate.name == "hf.exe" else f'& "{candidate}" login'
                break
        else:
            command = "hf auth login --force"
        self._log("Opening Hugging Face login in a new PowerShell window...")
        subprocess.Popen(["powershell", "-NoExit", "-Command", command])

    def open_project_folder(self) -> None:
        os.startfile(Path.cwd())

    def copy_environment_report(self) -> None:
        ok, messages = self._check_setup(include_paths=True)
        report = "\n".join(messages)
        self.root.clipboard_clear()
        self.root.clipboard_append(report)
        self.setup_summary.set("Environment report copied to clipboard.")
        self._log("Environment report copied to clipboard.")

    def _check_setup(self, include_paths: bool) -> tuple[bool, list[str]]:
        messages: list[str] = []
        ok = True
        messages.append(f"Python: {Path(sys.executable)}")
        messages.append(f"Working folder: {Path.cwd()}")

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            messages.append(f"FFmpeg: {ffmpeg}" if include_paths else "FFmpeg: found")
        else:
            ok = False
            messages.append("FFmpeg: missing. Install Gyan FFmpeg and add it to PATH.")

        packages = {
            "torch": "torch",
            "torchaudio": "torchaudio",
            "demucs": "demucs",
            "diffq": "diffq",
            "soundfile": "soundfile",
            "pydub": "pydub",
            "pyannote.audio": "pyannote.audio",
            "speechbrain": "speechbrain",
            "huggingface_hub": "huggingface_hub",
            "numpy": "numpy",
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
                messages.append(f"{package_name}: missing. Use Install / Update Requirements.")

        try:
            import torch
            messages.append(f"Torch: {torch.__version__}")
            messages.append(f"CUDA available: {torch.cuda.is_available()}")
            if torch.cuda.is_available():
                messages.append(f"GPU: {torch.cuda.get_device_name(0)}")
        except Exception as exc:
            ok = False
            messages.append(f"Torch check failed: {exc}")

        try:
            from huggingface_hub import get_token
            token = get_token()
        except Exception:
            token = None
        if token:
            messages.append("Hugging Face login: token found")
        else:
            ok = False
            messages.append("Hugging Face login: missing. Use Hugging Face Login.")
        return ok, messages

    def check_setup_clicked(self) -> None:
        self._clear_log()
        ok, messages = self._check_setup(include_paths=True)
        for message in messages:
            self._log(message)
        self.setup_summary.set("Setup looks ready." if ok else "Setup needs attention. Check Logs tab.")
        self.status_text.set("Setup ready" if ok else "Setup issue")
        if ok:
            messagebox.showinfo("Setup check", "Setup looks ready.")
        else:
            messagebox.showwarning("Setup check", "Setup needs attention. See the Logs tab.")

    # ----------------------------- Processing -----------------------------
    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.audio_queue:
            path = Path(self.input_file.get()) if self.input_file.get() else None
            if path and path.exists():
                self._add_paths([path])
        if not self.audio_queue:
            messagebox.showerror("Missing audio", "Add at least one audio file first.")
            return
        if not self.output_folder.get():
            messagebox.showerror("Missing output", "Choose an output folder first.")
            return
        if not any([self.export_vocals.get(), self.export_instrumental.get(), self.export_speaker_clips.get(), self.export_combined_speakers.get(), self.export_summary.get()]):
            messagebox.showerror("Missing output option", "Choose at least one output option.")
            return
        try:
            self._read_processing_options()
        except ValueError as exc:
            messagebox.showerror("Invalid processing setting", str(exc))
            return

        ok, messages = self._check_setup(include_paths=False)
        self._clear_log()
        for message in messages:
            self._log(message)
        if not ok:
            if not messagebox.askyesno("Setup warning", "Setup check found issues. Continue anyway?"):
                self.status_text.set("Setup issue")
                return

        self.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.configure(value=0)
        self.result_paths.clear()
        self.results_tree.delete(*self.results_tree.get_children())
        self._save_settings()
        self.worker = threading.Thread(target=self.process_queue, daemon=True)
        self.worker.start()

    def process_queue(self) -> None:
        try:
            total = len(self.audio_queue)
            for index, src in enumerate(list(self.audio_queue), start=1):
                self._raise_if_canceled()
                self._set_status(f"Processing {index}/{total}: {src.name}")
                self._set_progress((index - 1) / total * 100)
                self._process_one(src)
                self._set_progress(index / total * 100)
            self._set_status("Finished")
            self.root.after(0, lambda: messagebox.showinfo("Finished", "All queued audio files finished successfully."))
        except CanceledError:
            self._set_status("Canceled")
            self._log("Processing canceled.")
        except Exception as exc:
            self._set_status("Error")
            self._log(f"Error: {exc}")
            self.root.after(0, lambda message=str(exc): messagebox.showerror("Error", message))
        finally:
            self.root.after(0, self.finish)

    def _process_one(self, src: Path) -> None:
        out = self._resolve_run_output_folder(src)
        self.last_output_path = out
        out.mkdir(parents=True, exist_ok=True)
        options = self._read_processing_options()
        self._log(f"Input: {src}")
        self._log(f"Output: {out}")

        stems_dir = out / "stems"
        self._stage("Vocal cleanup", "running")
        self._run_demucs(src, stems_dir)
        self._stage("Vocal cleanup", "done")
        self._raise_if_canceled()

        vocals = stems_dir / DEMUCS_MODEL / src.stem / "vocals.wav"
        instrumental = stems_dir / DEMUCS_MODEL / src.stem / "no_vocals.wav"
        if not vocals.exists() or not instrumental.exists():
            raise RuntimeError(f"Demucs finished, but expected files were not found: {vocals} / {instrumental}")

        exports_dir = out / "exports"
        exports_dir.mkdir(exist_ok=True)
        if self.export_vocals.get():
            target = exports_dir / f"{src.stem}_vocals.wav"
            shutil.copy2(vocals, target)
            self._add_result("Clean vocals", target)
        if self.export_instrumental.get():
            target = exports_dir / f"{src.stem}_background.wav"
            shutil.copy2(instrumental, target)
            self._add_result("Background/no-vocals", target)

        needs_speakers = self.export_speaker_clips.get() or self.export_combined_speakers.get() or self.export_summary.get()
        if needs_speakers:
            self._stage("Speaker detection", "running")
            self._create_speaker_outputs(src, vocals, out, options)
            self._stage("Speaker detection", "done")
        else:
            self._log("Speaker detection skipped.")

        self._stage("Export files", "done")
        self.refresh_results_from_folder(out)

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
            if return_code != 0:
                raise RuntimeError(f"Demucs failed with exit code {return_code}.")
        finally:
            self.active_process = None

    def _create_speaker_outputs(self, src: Path, vocals: Path, out: Path, options: dict[str, float | int]) -> None:
        self._log("Loading pyannote speaker diarization model...")
        try:
            import torch
            from huggingface_hub import get_token
            from pydub import AudioSegment
            from pyannote.audio import Pipeline
        except Exception as exc:
            raise RuntimeError(f"Could not load audio packages. Try Setup & Repair. {exc}") from exc

        token = get_token()
        if not token:
            raise RuntimeError("No Hugging Face login detected. Use Setup & Repair > Hugging Face Login.")

        try:
            pipeline = Pipeline.from_pretrained(PYANNOTE_MODEL, use_auth_token=token)
            if torch.cuda.is_available():
                pipeline.to(torch.device("cuda"))
                self._log("Using CUDA acceleration for pyannote.")
            else:
                self._log("CUDA not available. Using CPU.")
        except Exception as exc:
            raise RuntimeError(f"Could not load {PYANNOTE_MODEL}. Confirm Hugging Face access and pinned dependencies. {exc}") from exc

        diarization = pipeline(str(vocals))
        self._raise_if_canceled()
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
            try:
                clip = clip.normalize().strip_silence(silence_len=200, silence_thresh=-40)
            except Exception:
                clip = audio[start_ms:end_ms].normalize()
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
            self._add_result("Combined speaker", target)

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
        self._add_result("CSV report", csv_path)
        self._add_result("TXT report", txt_path)

    # ----------------------------- Results -----------------------------
    def _add_result(self, kind: str, path: Path) -> None:
        self.result_paths.append(path)
        self.root.after(0, lambda k=kind, p=path: self.results_tree.insert("", "end", values=(k, str(p))))

    def refresh_results_from_folder(self, folder: Path) -> None:
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".wav", ".csv", ".txt"} and path not in self.result_paths:
                kind = "Audio" if path.suffix.lower() == ".wav" else "Report"
                self._add_result(kind, path)

    def refresh_last_output_results(self) -> None:
        if self.last_output_path and self.last_output_path.exists():
            self.refresh_results_from_folder(self.last_output_path)

    def _selected_result_path(self) -> Path | None:
        selected = self.results_tree.selection()
        if not selected:
            return None
        values = self.results_tree.item(selected[0], "values")
        return Path(values[1]) if values else None

    def open_selected_result(self) -> None:
        path = self._selected_result_path()
        if path and path.exists():
            os.startfile(path)

    def open_selected_result_folder(self) -> None:
        path = self._selected_result_path()
        if path and path.exists():
            os.startfile(path.parent)

    def rename_selected_result(self) -> None:
        path = self._selected_result_path()
        if not path or not path.exists():
            messagebox.showwarning("Rename", "Select an existing result file first.")
            return
        if path.suffix.lower() != ".wav":
            messagebox.showwarning("Rename", "Only WAV speaker/audio files can be renamed here.")
            return
        new_name = simpledialog.askstring("Rename file", "Enter new speaker/file name without extension:")
        if not new_name:
            return
        safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in new_name).strip() or path.stem
        target = path.with_name(safe + path.suffix)
        path.rename(target)
        self.refresh_last_output_results()
        self._log(f"Renamed {path.name} to {target.name}")

    # ----------------------------- Helpers -----------------------------
    def _apply_preset(self, update_options: bool) -> None:
        preset = PRESETS.get(self.preset_name.get(), PRESETS["Podcast / Interview"])
        self.preset_description.set(str(preset["description"]))
        self.min_clip_seconds.set(str(preset["min_clip_seconds"]))
        self.clip_padding_ms.set(str(preset["clip_padding_ms"]))
        self.combined_gap_ms.set(str(preset["combined_gap_ms"]))
        if update_options:
            self.export_speaker_clips.set(bool(preset["speaker_clips"]))
            self.export_combined_speakers.set(bool(preset["combined"]))
            self.export_summary.set(bool(preset["summary"]))

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
        self.output_folder.set(data.get("output_folder", str(Path.home() / "Downloads")))
        self.preset_name.set(data.get("preset_name", "Podcast / Interview"))
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
            "output_folder": self.output_folder.get(),
            "preset_name": self.preset_name.get(),
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

    def _run_background_command(self, command: list[str], status: str) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("Busy", "A task is already running.")
            return
        self.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.configure(value=0)
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
            self._set_status("Command finished")
        except CanceledError:
            self._set_status("Canceled")
        except Exception as exc:
            self._set_status("Error")
            self._log(f"Error: {exc}")
            self.root.after(0, lambda message=str(exc): messagebox.showerror("Error", message))
        finally:
            self.active_process = None
            self.root.after(0, self.finish)

    def _stage(self, name: str, state: str) -> None:
        symbols = {"waiting": "○", "running": "◐", "done": "✓", "error": "!"}
        colors = {"waiting": self.colors["muted"], "running": self.colors["accent"], "done": self.colors["success"], "error": self.colors["danger"]}
        symbol = symbols.get(state, "○")
        label = self.stage_labels.get(name)
        if label:
            self.root.after(0, lambda l=label, s=symbol, n=name, c=colors.get(state, self.colors["muted"]): l.configure(text=f"{s} {n}", foreground=c))

    def _set_progress(self, value: float) -> None:
        self.root.after(0, lambda v=value: self.progress.configure(value=v))

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

    def _run_startup_check(self) -> None:
        ok, messages = self._check_setup(include_paths=False)
        self.setup_summary.set("Setup looks ready." if ok else "Setup needs attention. Open Setup & Repair.")
        self.status_text.set("Setup ready" if ok else "Setup issue")
        for message in messages:
            self._log(message)

    def open_last_output(self) -> None:
        target = self.last_output_path or Path(self.output_folder.get() or ".")
        if target.exists():
            os.startfile(target)
        else:
            messagebox.showwarning("Output folder", "The output folder does not exist yet.")

    def cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.status_text.set("Canceling...")
        self._log("Cancel requested.")
        if self.active_process and self.active_process.poll() is None:
            self.active_process.terminate()

    def finish(self) -> None:
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.progress.configure(value=0)

    def on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Quit", "Processing is still running. Cancel and close?"):
                return
            self.cancel()
        self._save_settings()
        self.root.destroy()


class CanceledError(Exception):
    pass


if __name__ == "__main__":
    app_root = tk.Tk()
    SimpleVoiceSplitterApp(app_root)
    app_root.mainloop()
