from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk

from audiodl.core.logging import setup_logging
from audiodl.core.models import PipelineRequest
from audiodl.core.pipeline import Pipeline
from audiodl.core.settings import load_settings
from audiodl.providers.base import ProgressEvent, list_providers

# Ensure providers are registered at import time.
# (Later we can replace this with an auto-discovery mechanism.)
from audiodl.providers.youtube import provider as _youtube_provider  # noqa: F401


@dataclass(frozen=True)
class _UiEvent:
    kind: str  # "log" | "progress" | "done" | "error"
    message: str = ""
    progress: Optional[float] = None


class AudioDLTkApp(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master)
        self.master = master

        setup_logging()

        self.settings = load_settings()

        self._ui_queue: "queue.Queue[_UiEvent]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

        self._build_ui()
        self._refresh_providers()

        self._set_defaults_from_settings()

        # Pump UI events periodically
        self.after(100, self._drain_ui_queue)

    # -------------------------
    # UI construction
    # -------------------------

    def _build_ui(self) -> None:
        self.master.title("AudioDL")
        self.master.minsize(720, 520)

        self.grid(row=0, column=0, sticky="nsew")
        self.master.rowconfigure(0, weight=1)
        self.master.columnconfigure(0, weight=1)

        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="URL / Fuente").grid(row=0, column=0, sticky="w")
        self.var_source = tk.StringVar()
        self.entry_source = ttk.Entry(top, textvariable=self.var_source)
        self.entry_source.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        mid = ttk.Frame(self)
        mid.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        for c in range(6):
            mid.columnconfigure(c, weight=1 if c in (1, 3, 5) else 0)

        # Output dir
        ttk.Label(mid, text="Destino").grid(row=0, column=0, sticky="w")
        self.var_output = tk.StringVar()
        self.entry_output = ttk.Entry(mid, textvariable=self.var_output)
        self.entry_output.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(mid, text="Elegir…", command=self._choose_output_dir).grid(row=0, column=2, sticky="ew")

        # Provider
        ttk.Label(mid, text="Proveedor").grid(row=0, column=3, sticky="w", padx=(16, 0))
        self.var_provider = tk.StringVar(value="auto")
        self.cmb_provider = ttk.Combobox(mid, textvariable=self.var_provider, state="readonly", width=18)
        self.cmb_provider.grid(row=0, column=4, sticky="ew", padx=(8, 0))

        # Overwrite
        self.var_overwrite = tk.BooleanVar(value=False)
        self.chk_overwrite = ttk.Checkbutton(mid, text="Sobrescribir", variable=self.var_overwrite)
        self.chk_overwrite.grid(row=0, column=5, sticky="e", padx=(16, 0))

        # Audio options row
        row2 = ttk.Frame(self)
        row2.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)
        row2.columnconfigure(0, weight=1)
        row2.rowconfigure(2, weight=1)

        opts = ttk.Frame(row2)
        opts.grid(row=0, column=0, sticky="ew")
        opts.columnconfigure(1, weight=1)
        opts.columnconfigure(3, weight=1)

        ttk.Label(opts, text="Formato").grid(row=0, column=0, sticky="w")
        self.var_format = tk.StringVar(value="mp3")
        self.entry_format = ttk.Entry(opts, textvariable=self.var_format, width=10)
        self.entry_format.grid(row=0, column=1, sticky="w", padx=(8, 24))

        ttk.Label(opts, text="Calidad").grid(row=0, column=2, sticky="w")
        self.var_quality = tk.StringVar(value="0")
        self.entry_quality = ttk.Entry(opts, textvariable=self.var_quality, width=10)
        self.entry_quality.grid(row=0, column=3, sticky="w", padx=(8, 0))

        # Buttons + progress
        controls = ttk.Frame(row2)
        controls.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        controls.columnconfigure(1, weight=1)

        self.btn_start = ttk.Button(controls, text="Descargar", command=self._on_start)
        self.btn_start.grid(row=0, column=0, sticky="w")

        self.progress = ttk.Progressbar(controls, mode="determinate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=(12, 12))
        self.progress["value"] = 0

        self.btn_stop = ttk.Button(controls, text="Parar", command=self._on_stop, state="disabled")
        self.btn_stop.grid(row=0, column=2, sticky="e")

        # Log area
        log_frame = ttk.LabelFrame(row2, text="Log")
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.txt_log = tk.Text(log_frame, height=12, wrap="word")
        self.txt_log.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.txt_log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.txt_log.configure(yscrollcommand=scroll.set)

    def _refresh_providers(self) -> None:
        # Ensure list_providers() includes registered ones (YouTube imported above)
        provs = list_providers()
        options = ["auto"] + [p.id for p in provs]
        self.cmb_provider["values"] = options
        if self.var_provider.get() not in options:
            self.var_provider.set("auto")

    def _set_defaults_from_settings(self) -> None:
        self.var_output.set(str(self.settings.download_dir))
        self.var_format.set(self.settings.audio_format)
        self.var_quality.set(self.settings.audio_quality)
        self.var_overwrite.set(self.settings.overwrite)

    # -------------------------
    # Actions
    # -------------------------

    def _choose_output_dir(self) -> None:
        initial = self.var_output.get() or str(Path.home())
        chosen = filedialog.askdirectory(initialdir=initial, title="Selecciona carpeta de destino")
        if chosen:
            self.var_output.set(chosen)

    def _on_start(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("AudioDL", "Ya hay una descarga en curso.")
            return

        source = self.var_source.get().strip()
        if not source:
            messagebox.showwarning("AudioDL", "Introduce una URL o fuente.")
            return

        out_dir = self.var_output.get().strip()
        if not out_dir:
            messagebox.showwarning("AudioDL", "Selecciona una carpeta de destino.")
            return

        fmt = self.var_format.get().strip() or "mp3"
        quality = self.var_quality.get().strip() or "0"
        provider_id = self.var_provider.get().strip()
        if provider_id == "auto":
            provider_id = None

        req = PipelineRequest(
            source=source,
            output_dir=out_dir,
            provider_id=provider_id,
            audio_format=fmt,
            audio_quality=quality,
            overwrite=bool(self.var_overwrite.get()),
            cookies_path=str(self.settings.cookies_path) if self.settings.cookies_path else None,
            ffmpeg_path=self.settings.ffmpeg_path,
            tmp_dir=str(self.settings.tmp_dir) if self.settings.tmp_dir else None,
        )

        self._stop_flag.clear()
        self._set_running(True)
        self._append_log("▶ Iniciando…\n")
        self._set_progress(0.0)

        self._worker = threading.Thread(target=self._run_pipeline, args=(req,), daemon=True)
        self._worker.start()

    def _on_stop(self) -> None:
        # Note: yt-dlp subprocess cancellation isn't wired yet.
        # This stops UI waiting and prevents new actions; next step is adding cancellation support.
        self._stop_flag.set()
        self._append_log("⏹ Cancelación solicitada: deteniendo yt-dlp…\n")

    def _set_running(self, running: bool) -> None:
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.entry_source.configure(state="disabled" if running else "normal")
        self.entry_output.configure(state="disabled" if running else "normal")
        self.entry_format.configure(state="disabled" if running else "normal")
        self.entry_quality.configure(state="disabled" if running else "normal")
        self.cmb_provider.configure(state="disabled" if running else "readonly")
        self.chk_overwrite.configure(state="disabled" if running else "normal")

    def _append_log(self, text: str) -> None:
        self.txt_log.insert("end", text)
        self.txt_log.see("end")

    def _set_progress(self, value: Optional[float]) -> None:
        if value is None:
            self.progress.configure(mode="indeterminate")
            self.progress.start(10)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            v = max(0.0, min(1.0, float(value)))
            self.progress["value"] = v * 100.0

    # -------------------------
    # Worker + event bridge
    # -------------------------

    def _progress_cb(self, event: ProgressEvent) -> None:
        # Called from worker thread; enqueue for UI thread
        msg = f"[{event.provider_id}][{event.phase}] {event.message}"
        self._ui_queue.put(_UiEvent(kind="log", message=msg + "\n"))
        if event.progress is not None:
            self._ui_queue.put(_UiEvent(kind="progress", progress=event.progress))

    def _run_pipeline(self, req: PipelineRequest) -> None:
        try:
            pipeline = Pipeline(progress=self._progress_cb, cancel_event=self._stop_flag)
            results = pipeline.run(req)

            if self._stop_flag.is_set():
                self._ui_queue.put(_UiEvent(kind="done", message="⏹ Parado por el usuario.\n"))
                return

            lines = ["✅ Completado:\n"]
            for r in results:
                lines.append(f"- {r.item_title}\n")
                for f in r.files:
                    lines.append(f"  → {f.path}\n")

            self._ui_queue.put(_UiEvent(kind="done", message="".join(lines)))
        except Exception as exc:
            self._ui_queue.put(_UiEvent(kind="error", message=f"❌ Error: {exc}\n"))

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                ev = self._ui_queue.get_nowait()
                if ev.kind == "log":
                    self._append_log(ev.message)
                elif ev.kind == "progress":
                    self._set_progress(ev.progress)
                elif ev.kind == "done":
                    self._set_progress(1.0)
                    self._append_log(ev.message)
                    self._set_running(False)
                elif ev.kind == "error":
                    self._set_progress(0.0)
                    self._append_log(ev.message)
                    self._set_running(False)
        except queue.Empty:
            pass
        finally:
            self.after(100, self._drain_ui_queue)


def run() -> None:
    root = tk.Tk()
    # Use a nicer default theme when available
    try:
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass

    AudioDLTkApp(root)
    root.mainloop()
