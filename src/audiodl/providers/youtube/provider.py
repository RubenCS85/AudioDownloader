from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from audiodl.core.models import Collection, ProviderRef, Track
from audiodl.providers.base import (
    DownloadOptions,
    DownloadResult,
    ProgressCallback,
    ProviderError,
    emit_progress,
    register_provider,
)


_YT_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}


def _is_youtube_url(s: str) -> bool:
    try:
        p = urlparse(s.strip())
        if p.scheme not in ("http", "https"):
            return False
        host = (p.netloc or "").lower()
        return host in _YT_HOSTS
    except Exception:
        return False


def _run(cmd: List[str]) -> subprocess.Popen:
    # text=True gives str lines; bufsize=1 enables line-buffered reads when possible
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )


def _check_output(cmd: List[str]) -> str:
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)


_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
_DEST_RE = re.compile(r"Destination:\s+(.*)$")


@dataclass(frozen=True)
class _Resolved:
    item: Track | Collection


class YouTubeProvider:
    @property
    def id(self) -> str:
        return "youtube"

    @property
    def display_name(self) -> str:
        return "YouTube"

    def can_handle(self, source: str) -> bool:
        return _is_youtube_url(source)

    def resolve(self, source: str, *, progress: Optional[ProgressCallback] = None) -> Track | Collection:
        """
        Resolve a YouTube URL into a Track or Collection using yt-dlp JSON output.
        """
        emit_progress(progress, provider_id=self.id, phase="resolve", message="Resolviendo URL…")

        # -J returns JSON; --flat-playlist keeps playlist entries lightweight
        cmd = ["yt-dlp", "-J", "--flat-playlist", "--no-warnings", source]

        try:
            out = _check_output(cmd)
            data = json.loads(out)
        except subprocess.CalledProcessError as e:
            raise ProviderError(f"yt-dlp resolve failed:\n{e.output}") from e
        except json.JSONDecodeError as e:
            raise ProviderError(f"Invalid yt-dlp JSON while resolving: {e}") from e

        provider = ProviderRef(id=self.id, display_name=self.display_name)

        # Playlist / collection
        if isinstance(data, dict) and data.get("_type") in ("playlist", "multi_video") or "entries" in data:
            title = (data.get("title") or "Playlist").strip()
            entries = []
            for ent in data.get("entries") or []:
                if not isinstance(ent, dict):
                    continue
                ent_title = (ent.get("title") or "Track").strip()
                ent_url = ent.get("url") or ent.get("webpage_url") or ""
                # If flat-playlist returned an id, rebuild canonical watch URL
                if ent_url and not ent_url.startswith("http"):
                    ent_url = f"https://www.youtube.com/watch?v={ent_url}"

                entries.append(
                    Track(
                        provider=provider,
                        title=ent_title,
                        source=ent_url or source,
                        url=ent_url if ent_url.startswith("http") else None,
                        duration_seconds=ent.get("duration"),
                        thumbnail_url=ent.get("thumbnail"),
                        meta={"id": ent.get("id")},
                    )
                )

            emit_progress(
                progress,
                provider_id=self.id,
                phase="resolve",
                message=f"Playlist detectada: {title} ({len(entries)} items)",
                progress_value=1.0,
            )

            return Collection(
                provider=provider,
                title=title,
                source=source,
                url=data.get("webpage_url"),
                thumbnail_url=data.get("thumbnail"),
                total=data.get("playlist_count") or len(entries),
                entries=entries,
                meta={"id": data.get("id")},
            )

        # Single video -> track
        title = (data.get("title") or "Track").strip()
        url = data.get("webpage_url") or source

        emit_progress(progress, provider_id=self.id, phase="resolve", message=f"Track detectado: {title}", progress_value=1.0)

        return Track(
            provider=provider,
            title=title,
            source=source,
            url=url,
            duration_seconds=data.get("duration"),
            thumbnail_url=data.get("thumbnail"),
            artist=data.get("artist") or data.get("uploader"),
            album=data.get("album"),
            meta={"id": data.get("id")},
        )

    def download(
        self,
        item: Track | Collection,
        options: DownloadOptions,
        *,
        progress: Optional[ProgressCallback] = None,
    ) -> DownloadResult:
        """
        Download a Track or a Collection (playlist). For collections, yt-dlp handles it directly.
        Real cancellation supported via options.cancel_event.
        """
        source = str(item.url) if getattr(item, "url", None) else item.source

        outtmpl = f"{options.output_dir}/%(title)s.%(ext)s"

        result = run_ytdlp(
            source=source,
            output_template=outtmpl,
            audio_format=options.audio_format,
            audio_quality=options.audio_quality,
            overwrite=options.overwrite,
            cookies_path=options.cookies_path,
            ffmpeg_path=options.ffmpeg_path,
            tmp_dir=options.tmp_dir,
            progress=progress,
            provider_id=self.id,
            extra_args=[
                "--no-part",
            ],
            cancel_event=options.cancel_event,   # ✅ CLAVE
        )

        warnings: List[str] = []
        if getattr(result, "cancelled", False):
            warnings.append("Descarga cancelada por el usuario.")
        elif result.already_downloaded and not options.overwrite:
            warnings.append("El archivo ya estaba descargado (no-overwrites).")

        return DownloadResult(
            provider_id=self.id,
            item_title=item.title,
            output_paths=result.output_paths,
            warnings=tuple(warnings),
        )

# Register at import time
register_provider(YouTubeProvider())
