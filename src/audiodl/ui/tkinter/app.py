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


# -------------------------
# Tooltip helper
# -------------------------

class ToolTip:
    """
    Simple tooltip for Tkinter/ttk widgets.

    Usage:
        ToolTip(widget, "text")
    """
    def __init__(self, widget: tk.Widget, text: str, *, wraplength: int = 420) -> None:
        self.widget = widget
        self.text = text
        self.wraplength = wraplength

        self._tip: Optional[tk.Toplevel] = None
        self._after_id: Optional[str] = None

        widget.bind("<Enter>", self._on_enter, add=True)
        widget.bind("<Leave>", self._on_leave, add=True)
        widget.bind("<ButtonPress>", self._on_leave, add=True)

    def _on_enter(self, _event=None) -> None:
        # small delay to avoid flicker
        self._after_id = self.widget.after(350, self._show)

    def _on_leave(self, _event=None) -> None:
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self._hide()

    def _show(self) -> None:
        if self._tip is not None:
            return

        # Try to place tooltip near mouse pointer
        try:
            x, y = self.widget.winfo_pointerx(), self.widget.winfo_pointery()
        except Exception:
            x, y = self.widget.winfo_rootx(), self.widget.winfo_rooty()

        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x + 12}+{y + 12}")

        frame = ttk.Frame(self._tip, padding=(8, 6))
        frame.pack(fill="both", expand=True)

        lbl = ttk.Label(frame, text=self.text, justify="left", wraplength=self.wraplength)
        lbl.pack(fill="both", expand=True)

    def _hide(self) -> None:
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


# -------------------------
# UI event bridge
# -------------------------

@dataclass(frozen=True)
class _UiEvent:
    kind: str  # "log" | "progress" | "done" | "error"
    message: str = ""
    progress: Optional[float] = None


class AudioDLTkApp(ttk.Frame):
    """
    Tkinter UI for AudioDL.
    Includes advanced options + dropdowns for format/quality presets + tooltips.
    """

    # Human labels -> internal audio_format values expected by the pipeline/provider
    _FORMAT_LABEL_TO_VALUE = {
        "Mejor audio (sin convertir)": "best",
        "Preferir M4A (sin convertir)": "m4a",
        "Preferir Opus (sin convertir)": "opus",
        "MP3 (convertir con ffmpeg)": "mp3",
    }
    _FORMAT_VALUE_TO_LABEL = {v: k for k, v in _FORMAT_LABEL_TO_VALUE.items()}

    # Quality presets (for mp3)
    _MP3_QUALITY_OPTIONS = [
        ("0", "VBR V0 (máxima calidad)"),
        ("320K", "CBR 320 kbps"),
        ("256K", "CBR 256 kbps"),
        ("192K", "CBR 192 kbps"),
        ("160K", "CBR 160 kbps"),
        ("128K", "CBR 128 kbps"),
    ]
    _MP3_QUALITY_LABEL_TO_VALUE = {label: val for val, label in _MP3_QUALITY_OPTIONS}
    _MP3_QUALITY_VALUE_TO_LABEL = {val: label for val, label in _MP3_QUALITY_OPTIONS}

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
        self._sync_quality_state()

        self.after(100, self._drain_ui_queue)

    # -------------------------
    # UI construction
    # -------------------------

    def _build_ui(self) -> None:
        self.master.title("AudioDL")
        self.master.minsize(780, 580)

        self.grid(row=0, column=0, sticky="nsew")
        self.master.rowconfigure(0, weight=1)
        self.master.columnconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        # Top: source
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        top.columnconfigure(1, weight=1)

        lbl_source = ttk.Label(top, text="URL / Fuente")
        lbl_source.grid(row=0, column=0, sticky="w")
        self.var_source = tk.StringVar()
        self.entry_source = ttk.Entry(top, textvariable=self.var_source)
        self.entry_source.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ToolTip(
            lbl_source,
            "Pega una URL (vídeo, playlist o canal). El proveedor se auto-detecta si está en 'auto'.",
        )
        ToolTip(
            self.entry_source,
            "Ejemplos:\n"
            "- https://www.youtube.com/watch?v=...\n"
            "- https://www.youtube.com/playlist?list=...\n"
            "- https://www.youtube.com/@canal/videos",
        )

        # Mid: output/provider/overwrite
        mid = ttk.Frame(self)
        mid.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        for c in range(6):
            mid.columnconfigure(c, weight=1 if c in (1, 3, 5) else 0)

        ttk.Label(mid, text="Destino").grid(row=0, column=0, sticky="w")
        self.var_output = tk.StringVar()
        self.entry_output = ttk.Entry(mid, textvariable=self.var_output)
        self.entry_output.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        btn_choose = ttk.Button(mid, text="Elegir…", command=self._choose_output_dir)
        btn_choose.grid(row=0, column=2, sticky="ew")

        ToolTip(self.entry_output, "Carpeta donde se guardarán los audios descargados.")
        ToolTip(btn_choose, "Selecciona la carpeta de destino.")

        ttk.Label(mid, text="Proveedor").grid(row=0, column=3, sticky="w", padx=(16, 0))
        self.var_provider = tk.StringVar(value="auto")
        self.cmb_provider = ttk.Combobox(mid, textvariable=self.var_provider, state="readonly", width=18)
        self.cmb_provider.grid(row=0, column=4, sticky="ew", padx=(8, 0))
        ToolTip(self.cmb_provider, "Selecciona el proveedor o deja 'auto' para detección automática.")

        self.var_overwrite = tk.BooleanVar(value=False)
        self.chk_overwrite = ttk.Checkbutton(mid, text="Sobrescribir", variable=self.var_overwrite)
        self.chk_overwrite.grid(row=0, column=5, sticky="e", padx=(16, 0))
        ToolTip(
            self.chk_overwrite,
            "Si está activado, reemplaza archivos existentes.\n"
            "Si está desactivado, yt-dlp evitará descargar si ya existe.",
        )

        # Main body
        row2 = ttk.Frame(self)
        row2.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)
        self.rowconfigure(2, weight=1)
        row2.columnconfigure(0, weight=1)
        row2.rowconfigure(3, weight=1)  # log expands

        # Format / quality row
        opts = ttk.Frame(row2)
        opts.grid(row=0, column=0, sticky="ew")
        opts.columnconfigure(1, weight=1)
        opts.columnconfigure(3, weight=1)

        ttk.Label(opts, text="Formato").grid(row=0, column=0, sticky="w")
        self.var_format_label = tk.StringVar()
        self.cmb_format = ttk.Combobox(
            opts,
            textvariable=self.var_format_label,
            state="readonly",
            width=30,
            values=list(self._FORMAT_LABEL_TO_VALUE.keys()),
        )
        self.cmb_format.grid(row=0, column=1, sticky="w", padx=(8, 24))
        self.cmb_format.bind("<<ComboboxSelected>>", lambda _e: self._sync_quality_state())

        ToolTip(
            self.cmb_format,
            "Define cómo se obtiene el audio:\n"
            "- Mejor audio / Preferir M4A / Preferir Opus: intenta evitar convertir (más rápido y sin pérdidas)\n"
            "- MP3: convierte con ffmpeg y aplica la calidad seleccionada.",
        )

        ttk.Label(opts, text="Calidad").grid(row=0, column=2, sticky="w")
        self.var_quality_label = tk.StringVar()
        self.cmb_quality = ttk.Combobox(
            opts,
            textvariable=self.var_quality_label,
            state="readonly",
            width=22,
        )
        self.cmb_quality.grid(row=0, column=3, sticky="w", padx=(8, 0))

        ToolTip(
            self.cmb_quality,
            "Calidad de MP3 (solo aplica si eliges 'MP3').\n"
            "V0 = VBR (muy buena), 320K = CBR (máximo bitrate constante).",
        )

        # Controls + progress
        controls = ttk.Frame(row2)
        controls.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        controls.columnconfigure(1, weight=1)

        self.btn_start = ttk.Button(controls, text="Descargar", command=self._on_start)
        self.btn_start.grid(row=0, column=0, sticky="w")
        ToolTip(self.btn_start, "Inicia la descarga usando la configuración seleccionada.")

        self.progress = ttk.Progressbar(controls, mode="determinate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=(12, 12))
        self.progress["value"] = 0
        ToolTip(self.progress, "Progreso aproximado. En playlists, depende de yt-dlp.")

        self.btn_stop = ttk.Button(controls, text="Parar", command=self._on_stop, state="disabled")
        self.btn_stop.grid(row=0, column=2, sticky="e")
        ToolTip(self.btn_stop, "Solicita cancelación. Intentará detener yt-dlp y ffmpeg.")

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
        ToolTip(
            self.chk_archive,
            "Evita descargar de nuevo lo ya descargado.\n"
            "Se guarda un archivo descargados.txt en <destino>/_logs/descargados.txt.",
        )

        self.chk_loudnorm = ttk.Checkbutton(adv, text="Normalizar volumen (loudnorm)", variable=self.var_loudnorm)
        self.chk_loudnorm.grid(row=0, column=1, sticky="w", padx=(8, 8), pady=(6, 0))
        ToolTip(
            self.chk_loudnorm,
            "Aplica normalización EBU R128 con ffmpeg (filtro loudnorm).\n"
            "Requiere ffmpeg disponible.",
        )

        self.chk_parsemeta = ttk.Checkbutton(
            adv,
            text="Parsear metadatos 'Artista - Título'",
            variable=self.var_parse_meta,
        )
        self.chk_parsemeta.grid(row=1, column=0, sticky="w", padx=(8, 8), pady=(4, 0))
        ToolTip(
            self.chk_parsemeta,
            "Intenta dividir el título del vídeo en 'Artista' y 'Título' usando el patrón:\n"
            "Artista - Título",
        )

        self.chk_stripemojis = ttk.Checkbutton(adv, text="Quitar emojis del título", variable=self.var_strip_emojis)
        self.chk_stripemojis.grid(row=1, column=1, sticky="w", padx=(8, 8), pady=(4, 0))
        ToolTip(
            self.chk_stripemojis,
            "Elimina emojis/símbolos de los metadatos (title) antes del postprocesado.\n"
            "Útil para evitar nombres raros o incompatibles.",
        )

        self.chk_thumb = ttk.Checkbutton(adv, text="Incrustar miniatura como cover", variable=self.var_embed_thumb)
        self.chk_thumb.grid(row=2, column=0, sticky="w", padx=(8, 8), pady=(4, 6))
        ToolTip(
            self.chk_thumb,
            "Incrusta la miniatura como carátula (cover art) dentro del audio.\n"
            "Puede requerir ffmpeg según el formato.",
        )

        # Log
        log_frame = ttk.LabelFrame(row2, text="Log")
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.txt_log = tk.Text(log_frame, height=12, wrap="word")
        self.txt_log.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.txt_log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.txt_log.configure(yscrollcommand=scroll.set)

        ToolTip(self.txt_log, "Salida del pipeline / yt-dlp. Útil para diagnosticar problemas.")

    # -------------------------
    # Provider list / defaults
    # -------------------------

    def _refresh_providers(self) -> None:
        provs = list_providers()
        options = ["auto"] + [p.id for p in provs]
        self.cmb_provider["values"] = options
        if self.var_provider.get() not in options:
            self.var_provider.set("auto")

    def _set_defaults_from_settings(self) -> None:
        self.var_output.set(str(self.settings.download_dir))
        self.var_overwrite.set(self.settings.overwrite)

        # format defaults from settings (mp3/best/m4a/opus)
        fmt_value = (self.settings.audio_format or "mp3").strip().lower()
        fmt_label = self._FORMAT_VALUE_TO_LABEL.get(fmt_value, self._FORMAT_VALUE_TO_LABEL["mp3"])
        self.var_format_label.set(fmt_label)

        # quality defaults from settings (mp3 quality: 0, 320K, etc.)
        q_value = (self.settings.audio_quality or "0").strip()
        q_label = self._MP3_QUALITY_VALUE_TO_LABEL.get(q_value, self._MP3_QUALITY_VALUE_TO_LABEL["0"])
        self.var_quality_label.set(q_label)

        # Initialize quality list properly
        self._sync_quality_state()

    # -------------------------
    # Dropdown logic
    # -------------------------

    def _get_selected_format_value(self) -> str:
        label = (self.var_format_label.get() or "").strip()
        return self._FORMAT_LABEL_TO_VALUE.get(label, "mp3")

    def _sync_quality_state(self) -> None:
        fmt = self._get_selected_format_value()

        if fmt == "mp3":
            self.cmb_quality["values"] = [label for _val, label in self._MP3_QUALITY_OPTIONS]
            # Keep current selection valid
            cur = (self.var_quality_label.get() or "").strip()
            if cur not in self.cmb_quality["values"]:
                self.var_quality_label.set(self._MP3_QUALITY_VALUE_TO_LABEL["0"])
            self.cmb_quality.configure(state="readonly")
        else:
            # quality not applicable
            self.cmb_quality["values"] = [self._OTHER_QUALITY_DEFAULT]
            self.var_quality_label.set(self._OTHER_QUALITY_DEFAULT)
            self.cmb_quality.configure(state="disabled")

    def _get_effective_quality_value(self) -> str:
        fmt = self._get_selected_format_value()
        if fmt == "mp3":
            label = (self.var_quality_label.get() or "").strip()
            return self._MP3_QUALITY_LABEL_TO_VALUE.get(label, "0")
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

        provider_id = self.var_provider.get().strip()
        if provider_id == "auto":
            provider_id = None

        fmt = self._get_selected_format_value()
        quality = self._get_effective_quality_value()

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

        if running:
            self.cmb_quality.configure(state="disabled")
        else:
            self._sync_quality_state()

        self.cmb_provider.configure(state="disabled" if running else "readonly")
        self.chk_overwrite.configure(state="disabled" if running else "normal")

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
