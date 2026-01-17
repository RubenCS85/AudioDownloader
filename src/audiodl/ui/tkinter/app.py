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
    """
    Tkinter UI for AudioDL.
    Includes advanced options + dropdowns for format/quality presets.
    """

    # Presets for audio format dropdown
    _FORMAT_OPTIONS = [
        "mp3",
        "m4a",
        "opus",
        "best",
    ]

    # Presets for quality dropdown (meaningful mainly for mp3)
    # yt-dlp accepts strings like "0", "320K", "192K" for mp3 quality.
    _MP3_QUALITY_OPTIONS = [
        ("0", "MP3 VBR V0 (máxima calidad)"),
        ("320K", "MP3 CBR 320 kbps"),
        ("256K", "MP3 CBR 256 kbps"),
        ("192K", "MP3 CBR 192 kbps"),
        ("160K", "MP3 CBR 160 kbps"),
        ("128K", "MP3 CBR 128 kbps"),
    ]

    # For non-mp3 formats we keep a minimal option (and disable field)
    _OTHER_QUALITY_DEFAULT = "best"

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

        # Apply initial enable/disable state for quality combobox
        self._sync_quality_state()

        # Pump UI events periodically
        self.after(100, self._drain_ui_queue)

    # -------------------------
    # UI construction
    # -------------------------

    def _build_ui(self) -> None:
        self.master.title("AudioDL")
        self.master.minsize(760, 560)

        self.grid(row=0, column=0, sticky="nsew")
        self.master.rowconfigure(0, weight=1)
        self.master.columnconfigure(0, weight=1)

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

        # Main body
        row2 = ttk.Frame(self)
        row2.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)
        self.rowconfigure(2, weight=1)
        row2.columnconfigure(0, weight=1)
        row2.rowconfigure(3, weight=1)  # log expands

        # Audio options row
        opts = ttk.Frame(row2)
        opts.grid(row=0, column=0, sticky="ew")
        opts.columnconfigure(1, weight=1)
        opts.columnconfigure(3, weight=1)

        ttk.Label(opts, text="Formato").grid(row=0, column=0, sticky="w")
        self.var_format = tk.StringVar(value="mp3")
        self.cmb_format = ttk.Combobox(
            opts,
            textvariable=self.var_format,
            state="readonly",
            width=12,
            values=self._FORMAT_OPTIONS,
        )
        self.cmb_format.grid(row=0, column=1, sticky="w", padx=(8, 24))
        self.cmb_format.bind("<<ComboboxSelected>>", lambda _e: self._sync_quality_state())

        ttk.Label(opts, text="Calidad").grid(row=0, column=2, sticky="w")
        self.var_quality = tk.StringVar(value="0")
        self.cmb_quality = ttk.Combobox(
            opts,
            textvariable=self.var_quality,
            state="readonly",
            width=26,
        )
        self.cmb_quality.grid(row=0, column=3, sticky="w", padx=(8, 0))

        # Initialize quality list (mp3 defaults)
        self._set_quality_values_for_format("mp3")

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

        # Advanced options
        adv = ttk.LabelFrame(row2, text="Opciones avanzadas")
        adv.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        adv.columnconfigure(0, weight=1)
        adv.columnconfigure(1, weight=1)

        self.var_use_archive = tk.BooleanVar(value=True)
        self.var_loudnorm = tk.BooleanVar(value=False)
        self.var_embed_thumb = tk.BooleanVar(value=False)
        self.var_parse_meta = tk.BooleanVar(value=True)
        self.var_strip_emojis = tk.BooleanVar(value=False)

        self.chk_archive = ttk.Checkbutton(adv, text="Usar historial (archive)", variable=self.var_use_archive)
        self.chk_archive.grid(row=0, column=0, sticky="w", padx=(8, 8), pady=(6, 0))

        self.chk_loudnorm = ttk.Checkbutton(adv, text="Normalizar volumen (loudnorm)", variable=self.var_loudnorm)
        self.chk_loudnorm.grid(row=0, column=1, sticky="w", padx=(8, 8), pady=(6, 0))

        self.chk_parsemeta = ttk.Checkbutton(
            adv,
            text="Parsear metadatos 'Artista - Título'",
            variable=self.var_parse_meta,
        )
        self.chk_parsemeta.grid(row=1, column=0, sticky="w", padx=(8, 8), pady=(4, 0))

        self.chk_stripemojis = ttk.Checkbutton(adv, text="Quitar emojis del título", variable=self.var_strip_emojis)
        self.chk_stripemojis.grid(row=1, column=1, sticky="w", padx=(8, 8), pady=(4, 0))

        self.chk_thumb = ttk.Checkbutton(adv, text="Incrustar miniatura como cover", variable=self.var_embed_thumb)
        self.chk_thumb.grid(row=2, column=0, sticky="w", padx=(8, 8), pady=(4, 6))

        # Log area
        log_frame = ttk.LabelFrame(row2, text="Log")
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.txt_log = tk.Text(log_frame, height=12, wrap="word")
        self.txt_log.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.txt_log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.txt_log.configure(yscrollcommand=scroll.set)

    def _refresh_providers(self) -> None:
        provs = list_providers()
        options = ["auto"] + [p.id for p in provs]
        self.cmb_provider["values"] = options
        if self.var_provider.get() not in options:
            self.var_provider.set("auto")

    def _set_defaults_from_settings(self) -> None:
        self.var_output.set(str(self.settings.download_dir))

        # Ensure defaults are supported
        fmt = (self.settings.audio_format or "mp3").strip().lower()
        if fmt not in self._FORMAT_OPTIONS:
            fmt = "mp3"
        self.var_format.set(fmt)

        q = (self.settings.audio_quality or "0").strip()
        self.var_quality.set(q)

        self.var_overwrite.set(self.settings.overwrite)

        # sync dropdown values
        self._set_quality_values_for_format(fmt)
        self._sync_quality_state()

    # -------------------------
    # Dropdown helpers
    # -------------------------

    def _set_quality_values_for_format(self, fmt: str) -> None:
        fmt = (fmt or "").strip().lower()
        if fmt == "mp3":
            # Show human labels but store actual values in var_quality
            labels = [label for _val, label in self._MP3_QUALITY_OPTIONS]
            self.cmb_quality["values"] = labels

            # If current var_quality is a raw value, map it to label
            raw = (self.var_quality.get() or "").strip()
            mapped_label = None
            for val, label in self._MP3_QUALITY_OPTIONS:
                if raw.lower() == val.lower():
                    mapped_label = label
                    break
            if mapped_label is None:
                # Default to V0
                mapped_label = self._MP3_QUALITY_OPTIONS[0][1]
            self.var_quality.set(mapped_label)
        else:
            # For non-mp3, quality is not very meaningful in the same way; keep it "best"
            self.cmb_quality["values"] = [self._OTHER_QUALITY_DEFAULT]
            self.var_quality.set(self._OTHER_QUALITY_DEFAULT)

    def _sync_quality_state(self) -> None:
        fmt = (self.var_format.get() or "").strip().lower()
        # Reset list to match format
        self._set_quality_values_for_format(fmt)

        if fmt == "mp3":
            self.cmb_quality.configure(state="readonly")
        else:
            self.cmb_quality.configure(state="disabled")

    def _get_effective_quality_value(self) -> str:
        """
        Convert UI selection into yt-dlp --audio-quality value.
        For mp3: map label -> value (0, 320K, etc.)
        For others: return "best" (or empty is also ok, but we keep stable)
        """
        fmt = (self.var_format.get() or "").strip().lower()
        q = (self.var_quality.get() or "").strip()

        if fmt == "mp3":
            # q is a label, map back to value
            for val, label in self._MP3_QUALITY_OPTIONS:
                if q == label:
                    return val
            # fallback
            return "0"

        return self._OTHER_QUALITY_DEFAULT

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

        fmt = (self.var_format.get() or "mp3").strip().lower()
        if fmt not in self._FORMAT_OPTIONS:
            fmt = "mp3"

        quality = self._get_effective_quality_value()

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
            # advanced
            use_archive=bool(self.var_use_archive.get()),
            loudnorm=bool(self.var_loudnorm.get()),
            embed_thumbnail=bool(self.var_embed_thumb.get()),
            parse_metadata_artist_title=bool(self.var_parse_meta.get()),
            strip_emojis=bool(self.var_strip_emojis.get()),
            # env
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
        self._stop_flag.set()
        self._append_log("⏹ Cancelación solicitada: deteniendo yt-dlp…\n")

    def _set_running(self, running: bool) -> None:
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.entry_source.configure(state="disabled" if running else "normal")
        self.entry_output.configure(state="disabled" if running else "normal")

        self.cmb_format.configure(state="disabled" if running else "readonly")
        # quality depends on format; when stopping, re-sync state
        if running:
            self.cmb_quality.configure(state="disabled")
        else:
            self._sync_quality_state()

        self.cmb_provider.configure(state="disabled" if running else "readonly")
        self.chk_overwrite.configure(state="disabled" if running else "normal")

        # advanced
        self.chk_archive.configure(state="disabled" if running else "normal")
        self.chk_loudnorm.configure(state="disabled" if running else "normal")
        self.chk_parsemeta.configure(state="disabled" if running else "normal")
        self.chk_stripemojis.configure(state="disabled" if running else "normal")
        self.chk_thumb.configure(state="disabled" if running else "normal")

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
    try:
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass

    AudioDLTkApp(root)
    root.mainloop()
