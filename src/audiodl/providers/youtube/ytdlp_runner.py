"""
yt-dlp runner wrapper.

Goals:
- Run yt-dlp in a way that supports robust cancellation (process group).
- Parse progress in a stable way so the Tkinter progressbar moves reliably.
- Capture output file paths (Destination: ..., and FILE: ... prints).
- Detect already-downloaded (including archive skips).

Notes:
- We intentionally do NOT use --restrict-filenames because it replaces spaces with underscores
  and tends to "destroy" readable titles.
- On Windows we use --windows-filenames + --trim-filenames for safe names.
- We force a stable progress marker using --progress-template so parsing does not depend
  on yt-dlp's localized/variable progress formatting.

Expected integration:
- Provider passes `progress` callback and optionally adds:
  --print after_download:FILE:%(filepath)s
  --print after_move:FILE:%(filepath)s
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

from audiodl.providers.base import ProgressCallback, ProviderError, emit_progress

# Stable marker we inject via --progress-template (see cmd building below)
_PROGRESS_MARK_RE = re.compile(r"\bAUDIODL_PROGRESS:(\d+(?:\.\d+)?)\b")

# Fallback: classic yt-dlp line (kept, but we prefer the stable marker above)
_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")

_DEST_RE = re.compile(r"Destination:\s+(.*)$")
_ALREADY_RE = re.compile(r"\[download\].*has already been downloaded", re.IGNORECASE)
_ARCHIVE_RE = re.compile(r"(already been recorded in the archive|already in archive)", re.IGNORECASE)
_FILE_RE = re.compile(r"^FILE:\s*(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class YtDlpRunResult:
    exit_code: int
    output_paths: tuple[str, ...]
    raw_lines: tuple[str, ...]
    already_downloaded: bool = False
    cancelled: bool = False


def _popen(cmd: Sequence[str]) -> subprocess.Popen:
    if os.name == "nt":
        flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        return subprocess.Popen(
            list(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            creationflags=flags,
        )

    return subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        preexec_fn=os.setsid,
    )


def _request_stop(proc: subprocess.Popen, *, gentle_timeout_s: float = 2.0) -> None:
    """
    Stop process (and its group) in a staged way:
    1) Gentle interrupt (SIGINT / CTRL_BREAK_EVENT)
    2) Terminate (SIGTERM / terminate())
    3) Kill (SIGKILL / kill())
    """
    if proc.poll() is not None:
        return

    # 1) Gentle interrupt
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(proc.pid, signal.SIGINT)
    except Exception:
        try:
            proc.send_signal(signal.SIGINT)
        except Exception:
            pass

    t0 = time.time()
    while time.time() - t0 < gentle_timeout_s:
        if proc.poll() is not None:
            return
        time.sleep(0.05)

    # 2) Terminate
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass

    t0 = time.time()
    while time.time() - t0 < gentle_timeout_s:
        if proc.poll() is not None:
            return
        time.sleep(0.05)

    # 3) Kill
    try:
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_ytdlp(
    *,
    source: str,
    output_template: str,
    audio_format: str = "mp3",
    audio_quality: str = "0",
    overwrite: bool = False,
    cookies_path: Optional[str] = None,
    ffmpeg_path: Optional[str] = None,
    tmp_dir: Optional[str] = None,
    progress: Optional[ProgressCallback] = None,
    provider_id: str = "youtube",
    extra_args: Optional[Sequence[str]] = None,
    cancel_event: Optional[Any] = None,
) -> YtDlpRunResult:
    """
    Execute yt-dlp with a consistent configuration and parse:
    - download progress percentage (robust via --progress-template marker)
    - output destination paths (best effort)
    - already-downloaded signals (including archive skips)
    - real cancellation via cancel_event
    """
    emit_progress(
        progress,
        provider_id=provider_id,
        phase="download",
        message="Iniciando yt-dlp…",
        progress_value=0.0,
    )

    # Stable progress marker:
    # yt-dlp exposes %(progress._percent_str)s -> e.g. "12.3%"
    # We embed it as "AUDIODL_PROGRESS:12.3" so our regex can parse it reliably.
    #
    # - We keep --newline so each update is a line.
    # - We set template only for "download" group.
    progress_template = "download:AUDIODL_PROGRESS:%(progress._percent_str)s"

    cmd: List[str] = [
        "yt-dlp",
        "--no-warnings",
        "--newline",
        "--progress-template",
        progress_template,
        "--no-mtime",
        "-x",
        "--audio-format",
        audio_format,
        "--audio-quality",
        audio_quality,
        "-o",
        output_template,
    ]

    # ✅ Nombres “bonitos” y seguros en Windows (como tu main.py)
    if os.name == "nt":
        cmd += ["--windows-filenames", "--trim-filenames", "180"]

    # Cookies / ffmpeg / temp
    if cookies_path:
        cmd += ["--cookies", cookies_path]
    if ffmpeg_path:
        cmd += ["--ffmpeg-location", ffmpeg_path]
    if tmp_dir:
        cmd += ["-P", f"temp:{tmp_dir}"]

    # Overwrite behavior
    cmd += ["--force-overwrites"] if overwrite else ["--no-overwrites"]

    # Allow caller to extend (e.g. tags, embed-thumbnail, archive, --print FILE:..., etc.)
    if extra_args:
        cmd += list(extra_args)

    cmd.append(str(source))

    proc = _popen(cmd)

    output_paths: List[str] = []
    raw_lines: List[str] = []
    already_downloaded = False
    cancelled = False

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        raw_lines.append(line)

        # Cancellation check (real stop)
        if cancel_event is not None:
            try:
                if bool(getattr(cancel_event, "is_set")()):
                    cancelled = True
                    emit_progress(progress, provider_id=provider_id, phase="download", message="Cancelando descarga…")
                    _request_stop(proc)
                    break
            except Exception:
                # If cancel_event isn't compatible, ignore it
                pass

        # -------------------------
        # Progress (preferred: stable marker)
        # -------------------------
        # Example line from template: "AUDIODL_PROGRESS:12.3%"
        # We parse the number and ignore the trailing '%'.
        # -------------------------
        # Progress (preferred: stable marker)
        # -------------------------
        m_mark = _PROGRESS_MARK_RE.search(line)
        if m_mark:
            try:
                pct = float(m_mark.group(1))
                emit_progress(
                    progress,
                    provider_id=provider_id,
                    phase="download",
                    message=f"Descargando… {pct:.1f}%",
                    progress_value=pct / 100.0,
                )
            except Exception:
                pass
        else:
            # Fallback: classic yt-dlp progress line
            m = _PROGRESS_RE.search(line)
            if m:
                try:
                    pct = float(m.group(1))
                    emit_progress(
                        progress,
                        provider_id=provider_id,
                        phase="download",
                        message=f"Descargando… {pct:.1f}%",
                        progress_value=pct / 100.0,
                    )
                except Exception:
                    pass

        # -------------------------
        # Output paths (best effort)
        # -------------------------
        d = _DEST_RE.search(line)
        if d:
            p = d.group(1).strip()
            if p:
                output_paths.append(p)

        # FILE: prints if provider uses --print after_move:FILE:%(filepath)s
        f = _FILE_RE.search(line)
        if f:
            p = f.group(1).strip()
            if p:
                output_paths.append(p)

        # -------------------------
        # Already downloaded / archive skips
        # -------------------------
        if _ALREADY_RE.search(line) or _ARCHIVE_RE.search(line):
            already_downloaded = True

        # Forward notable lines as postprocess messages
        if line.startswith("[ExtractAudio]") or line.startswith("[ffmpeg]"):
            emit_progress(progress, provider_id=provider_id, phase="postprocess", message=line)

    rc = proc.wait()

    # Deduplicate while preserving order
    deduped = list(dict.fromkeys(output_paths))

    if cancelled:
        emit_progress(progress, provider_id=provider_id, phase="download", message="Descarga cancelada")
        return YtDlpRunResult(
            exit_code=rc,
            output_paths=tuple(deduped),
            raw_lines=tuple(raw_lines),
            already_downloaded=already_downloaded,
            cancelled=True,
        )

    if rc != 0:
        tail = "\n".join(raw_lines[-30:])
        raise ProviderError(f"yt-dlp failed with exit code {rc}\n\nLast output:\n{tail}")

    emit_progress(progress, provider_id=provider_id, phase="download", message="yt-dlp completado", progress_value=1.0)

    return YtDlpRunResult(
        exit_code=rc,
        output_paths=tuple(deduped),
        raw_lines=tuple(raw_lines),
        already_downloaded=already_downloaded,
        cancelled=False,
    )
