"""
Download pipeline orchestration.

Responsibilities:
- Select provider (explicit or auto-detected)
- Resolve input into domain models
- Execute downloads
- Emit progress events in a provider-agnostic way
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from audiodl.core.models import (
    Collection,
    DownloadResult as CoreDownloadResult,
    DownloadedFile,
    PipelineRequest,
    Track,
)
from audiodl.providers.base import (
    DownloadOptions,
    ProgressCallback,
    ProviderError,
    find_provider_for_source,
    get_provider,
)


class Pipeline:
    """
    High-level orchestrator.

    This class should contain ZERO provider-specific logic.
    """

    def __init__(self, *, progress: Optional[ProgressCallback] = None, cancel_event: Optional[Any] = None) -> None:
        self._progress = progress
        self._cancel_event = cancel_event

    def run(self, request: PipelineRequest) -> List[CoreDownloadResult]:
        # Ensure output dir exists early (so providers can rely on it)
        self._ensure_output_dir(request.output_dir)

        provider = self._select_provider(request)

        resolved = provider.resolve(
            request.source,
            progress=self._progress,
        )

        archive_path = self._default_archive_path(request)

        options = DownloadOptions(
            output_dir=request.output_dir,
            audio_format=request.audio_format,
            audio_quality=request.audio_quality,
            overwrite=request.overwrite,
            cookies_path=request.cookies_path,
            ffmpeg_path=request.ffmpeg_path,
            tmp_dir=request.tmp_dir,
            # advanced
            use_archive=bool(request.use_archive),
            archive_path=archive_path,
            loudnorm=bool(request.loudnorm),
            embed_thumbnail=bool(request.embed_thumbnail),
            parse_metadata_artist_title=bool(request.parse_metadata_artist_title),
            strip_emojis=bool(request.strip_emojis),
            # cancellation
            cancel_event=self._cancel_event,
        )

        results: List[CoreDownloadResult] = []

        if isinstance(resolved, Track):
            if self._is_cancelled():
                return results
            results.append(self._download_track(provider, resolved, options, progress=self._progress))

        elif isinstance(resolved, Collection):
            results.extend(self._download_collection(provider, resolved, options))

        else:
            raise ProviderError(f"Unsupported resolved item type: {type(resolved)!r}")

        return results

    # -------------------------
    # Internal helpers
    # -------------------------

    def _select_provider(self, request: PipelineRequest):
        if request.provider_id:
            return get_provider(request.provider_id)
        return find_provider_for_source(request.source)

    def _is_cancelled(self) -> bool:
        if self._cancel_event is None:
            return False
        try:
            return bool(getattr(self._cancel_event, "is_set")())
        except Exception:
            return False

    def _ensure_output_dir(self, output_dir: str) -> None:
        try:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _default_archive_path(self, request: PipelineRequest) -> Optional[str]:
        if not getattr(request, "use_archive", True):
            return None

        if request.archive_path:
            return request.archive_path

        try:
            logs_dir = Path(request.output_dir) / "_logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            return str(logs_dir / "descargados.txt")
        except Exception:
            return None

    # ✅ CAMBIO: acepta progress (para el proxy en playlists)
    def _download_track(
        self,
        provider,
        track: Track,
        options: DownloadOptions,
        *,
        progress: Optional[ProgressCallback],
    ) -> CoreDownloadResult:
        raw_result = provider.download(
            track,
            options,
            progress=progress,
        )

        files = [DownloadedFile(path=p) for p in raw_result.output_paths]

        return CoreDownloadResult(
            provider_id=raw_result.provider_id,
            item_title=raw_result.item_title,
            files=files,
            warnings=list(raw_result.warnings),
        )

    def _download_collection(self, provider, collection: Collection, options: DownloadOptions) -> List[CoreDownloadResult]:
        # Flatten to tracks for proper progress aggregation
        tracks: List[Track] = []

        def _collect(item):
            if isinstance(item, Track):
                tracks.append(item)
            elif isinstance(item, Collection):
                for e in item.entries:
                    _collect(e)

        _collect(collection)

        total = max(1, len(tracks))
        results: List[CoreDownloadResult] = []

        for idx, track in enumerate(tracks, start=1):
            if self._is_cancelled():
                break

            # Map per-track progress [0..1] to global [(idx-1)/total .. idx/total]
            def progress_proxy(ev):
                try:
                    if ev.progress is not None:
                        p = float(ev.progress)
                        p = max(0.0, min(1.0, p))
                        ev = ev.model_copy(update={"progress": ((idx - 1) + p) / total})
                except Exception:
                    pass
                if self._progress:
                    self._progress(ev)

            results.append(self._download_track(provider, track, options, progress=progress_proxy))

        return results
