from __future__ import annotations

import os
import json
import subprocess
from pathlib import Path
from typing import List, Optional
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
from audiodl.providers.youtube.ytdlp_runner import run_ytdlp


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


def _check_output(cmd: List[str]) -> str:
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)


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
        emit_progress(progress, provider_id=self.id, phase="resolve", message="Resolviendo URL…")

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
        if isinstance(data, dict) and (data.get("_type") in ("playlist", "multi_video") or "entries" in data):
            title = (data.get("title") or "Playlist").strip()
            entries: List[Track] = []

            for ent in data.get("entries") or []:
                if not isinstance(ent, dict):
                    continue
                ent_title = (ent.get("title") or "Track").strip()
                ent_url = ent.get("url") or ent.get("webpage_url") or ""
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
        source = str(item.url) if getattr(item, "url", None) else item.source

        # Safer path building on Windows
        outtmpl = str(Path(options.output_dir) / "%(title)s.%(ext)s")

        # Build yt-dlp extra args from advanced options
        extra_args: List[str] = ["--no-part"]

        # Windows-safe filenames + trimming (like your legacy UI)
        if os.name == "nt":
            extra_args += ["--windows-filenames", "--trim-filenames", "180"]

        # Capture final file paths (runner parses FILE:)
        extra_args += [
            "--print",
            "after_download:FILE:%(filepath)s",
            "--print",
            "after_move:FILE:%(filepath)s",
        ]

        # Archive / historial
        if getattr(options, "use_archive", True) and getattr(options, "archive_path", None):
            extra_args += ["--download-archive", str(options.archive_path)]

        # --- Format / quality behavior (matches your old "modes") ---
        # Goal:
        # - mp3 -> convert to mp3 and respect --audio-quality
        # - m4a/opus/best -> prefer stream container/codec WITHOUT forcing conversion
        requested_fmt = (options.audio_format or "mp3").strip().lower()
        requested_q = (options.audio_quality or "0").strip()

        runner_audio_format = requested_fmt
        runner_audio_quality = requested_q

        if requested_fmt == "best":
            # Prefer best audio streams, don't convert
            extra_args += ["-f", "251/140/139/bestaudio/best"]
            runner_audio_format = "best"
            runner_audio_quality = "0"  # irrelevant but harmless

        elif requested_fmt == "m4a":
            # Prefer m4a, fallback to bestaudio; do not force conversion -> audio_format=best
            extra_args += ["-f", "140/139/bestaudio[ext=m4a]/bestaudio/best"]
            runner_audio_format = "best"
            runner_audio_quality = "0"

        elif requested_fmt == "opus":
            # Prefer opus, fallback to bestaudio; do not force conversion -> audio_format=best
            extra_args += ["-f", "251/bestaudio[ext=opus]/bestaudio/best"]
            runner_audio_format = "best"
            runner_audio_quality = "0"

        elif requested_fmt == "mp3":
            # For mp3, pick good audio sources first; conversion happens in runner
            extra_args += ["-f", "251/140/139/bestaudio/best"]
            runner_audio_format = "mp3"
            runner_audio_quality = requested_q or "0"

        # --- Metadata ---
        if getattr(options, "parse_metadata_artist_title", True):
            extra_args += [
                "--add-metadata",
                "--parse-metadata",
                r"title:(?P<artist>.+?)\s*-\s*(?P<title>.+)",
                # prevent URL from being set in comment/purl
                "--parse-metadata",
                r":(?P<meta_comment>)",
                "--parse-metadata",
                r":(?P<meta_purl>)",
            ]
        else:
            extra_args += ["--add-metadata"]

        # Strip emojis/symbols from title (before post-processing)
        if getattr(options, "strip_emojis", False):
            extra_args += [
                "--replace-in-metadata",
                "title",
                r"[^\x00-\x7F\u00C0-\u024F\u1E00-\u1EFF]+",
                "",
            ]

        # Embed thumbnail
        if getattr(options, "embed_thumbnail", False):
            extra_args += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]

        # Loudnorm
        if getattr(options, "loudnorm", False):
            extra_args += [
                "--postprocessor-args",
                "ExtractAudio+ffmpeg_o:-af loudnorm",
            ]

        result = run_ytdlp(
            source=source,
            output_template=outtmpl,
            audio_format=runner_audio_format,
            audio_quality=runner_audio_quality,
            overwrite=options.overwrite,
            cookies_path=options.cookies_path,
            ffmpeg_path=options.ffmpeg_path,
            tmp_dir=options.tmp_dir,
            progress=progress,
            provider_id=self.id,
            extra_args=extra_args,
            cancel_event=options.cancel_event,
        )

        warnings: List[str] = []
        if result.cancelled:
            warnings.append("Descarga cancelada por el usuario.")
        elif result.already_downloaded and not options.overwrite:
            warnings.append("El archivo ya estaba descargado (archive/no-overwrites).")

        return DownloadResult(
            provider_id=self.id,
            item_title=item.title,
            output_paths=result.output_paths,
            warnings=tuple(warnings),
        )


register_provider(YouTubeProvider())
