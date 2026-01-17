from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

from audiodl.providers.base import ProgressCallback, ProviderError, emit_progress

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
    """
    Start yt-dlp in its own process group so we can interrupt/terminate
    yt-dlp and its children (e.g., ffmpeg) reliably.
    """
    if os.name == "nt":
        return subprocess.Popen(
            list(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
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
    - download progress percentage
    - output destination paths (best effort)
    - already-downloaded signals (including archive skips)
    - real cancellation via cancel_event

    NOTE:
    We intentionally DO NOT use --restrict-filenames because it degrades readable titles
    (e.g. "Think Love (feat. Eloise)" -> "Think_Love_feat._Eloise").
    On Windows we use --windows-filenames + --trim-filenames for safety.
    """
    emit_progress(
        progress,
        provider_id=provider_id,
        phase="download",
        message="Iniciando yt-dlp…",
        progress_value=0.0,
    )

    cmd: List[str] = [
        "yt-dlp",
        "--no-warnings",
        "--newline",
        "-x",
        "--audio-format",
        audio_format,
        "--audio-quality",
        audio_quality,
        "-o",
        output_template,
    ]

    # ✅ Windows-safe filenames but keep readable titles
    if os.name == "nt":
        cmd += [
            "--windows-filenames",
            "--trim-filenames",
            "180",
        ]

    # Cookies / ffmpeg / temp
    if cookies_path:
        cmd += ["--cookies", cookies_path]
    if ffmpeg_path:
        cmd += ["--ffmpeg-location", ffmpeg_path]
    if tmp_dir:
        cmd += ["-P", f"temp:{tmp_dir}"]

    # Overwrite behavior
    cmd += ["--force-overwrites"] if overwrite else ["--no-overwrites"]

    # Allow caller to extend (e.g. tags, embed-thumbnail, archive, print FILE, etc.)
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

        # ✅ Cancellation check (real stop)
        if cancel_event is not None:
            try:
                if bool(getattr(cancel_event, "is_set")()):
                    cancelled = True
                    emit_progress(
                        progress,
                        provider_id=provider_id,
                        phase="download",
                        message="Cancelando descarga…",
                    )
                    _request_stop(proc)
                    break
            except Exception:
                pass

        # Progress percentage
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

        # Capture destination path (best effort)
        d = _DEST_RE.search(line)
        if d:
            p = d.group(1).strip()
            if p:
                output_paths.append(p)

        # Capture explicit FILE: prints if provider uses --print after_move:FILE:%(filepath)s
        f = _FILE_RE.search(line)
        if f:
            p = f.group(1).strip()
            if p:
                output_paths.append(p)

        # Already downloaded (including archive skips)
        if _ALREADY_RE.search(line) or _ARCHIVE_RE.search(line):
            already_downloaded = True

        # Forward notable lines as postprocess messages (useful for UI logs)
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

    emit_progress(
        progress,
        provider_id=provider_id,
        phase="download",
        message="yt-dlp completado",
        progress_value=1.0,
    )

    return YtDlpRunResult(
        exit_code=rc,
        output_paths=tuple(deduped),
        raw_lines=tuple(raw_lines),
        already_downloaded=already_downloaded,
        cancelled=False,
    )
