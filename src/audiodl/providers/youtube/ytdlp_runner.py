from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence

from audiodl.providers.base import ProgressCallback, ProviderError, emit_progress

_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
_DEST_RE = re.compile(r"Destination:\s+(.*)$")
_ALREADY_RE = re.compile(r"\[download\].*has already been downloaded", re.IGNORECASE)


@dataclass(frozen=True)
class YtDlpRunResult:
    exit_code: int
    output_paths: tuple[str, ...]
    raw_lines: tuple[str, ...]
    already_downloaded: bool = False


def _popen(cmd: Sequence[str]) -> subprocess.Popen:
    return subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )


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
) -> YtDlpRunResult:
    """
    Execute yt-dlp with a consistent configuration and parse:
    - download progress percentage
    - output destination paths (best effort)
    - already-downloaded signals

    This is intentionally provider-agnostic enough to be reused for other providers
    that rely on yt-dlp-style downloading.
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
        "--restrict-filenames",
    ]

    # Cookies / ffmpeg / temp
    if cookies_path:
        cmd += ["--cookies", cookies_path]
    if ffmpeg_path:
        cmd += ["--ffmpeg-location", ffmpeg_path]
    if tmp_dir:
        cmd += ["-P", f"temp:{tmp_dir}"]

    # Overwrite behavior
    if overwrite:
        cmd += ["--force-overwrites"]
    else:
        cmd += ["--no-overwrites"]

    # Allow caller to extend (e.g. tags, embed-thumbnail, sponsorblock, etc.)
    if extra_args:
        cmd += list(extra_args)

    cmd.append(source)

    proc = _popen(cmd)

    output_paths: List[str] = []
    raw_lines: List[str] = []
    already_downloaded = False

    last_progress: Optional[float] = None

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        raw_lines.append(line)

        # Progress percentage
        m = _PROGRESS_RE.search(line)
        if m:
            try:
                pct = float(m.group(1))
                last_progress = pct / 100.0
                emit_progress(
                    progress,
                    provider_id=provider_id,
                    phase="download",
                    message=f"Descargando… {pct:.1f}%",
                    progress_value=last_progress,
                )
            except Exception:
                pass

        # Capture destination path (best effort)
        d = _DEST_RE.search(line)
        if d:
            p = d.group(1).strip()
            if p:
                output_paths.append(p)

        if _ALREADY_RE.search(line):
            already_downloaded = True

        # Forward notable lines as postprocess messages (useful for UI logs)
        if line.startswith("[ExtractAudio]") or line.startswith("[ffmpeg]"):
            emit_progress(progress, provider_id=provider_id, phase="postprocess", message=line)

    rc = proc.wait()

    if rc != 0:
        # include tail of output for easier debugging
        tail = "\n".join(raw_lines[-30:])
        raise ProviderError(f"yt-dlp failed with exit code {rc}\n\nLast output:\n{tail}")

    emit_progress(
        progress,
        provider_id=provider_id,
        phase="download",
        message="yt-dlp completado",
        progress_value=1.0,
    )

    # Deduplicate while preserving order
    deduped: List[str] = []
    seen = set()
    for p in output_paths:
        if p not in seen:
            seen.add(p)
            deduped.append(p)

    return YtDlpRunResult(
        exit_code=rc,
        output_paths=tuple(deduped),
        raw_lines=tuple(raw_lines),
        already_downloaded=already_downloaded,
    )
