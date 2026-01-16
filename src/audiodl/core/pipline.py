"""
Download pipeline orchestration.

Responsibilities:
- Select provider (explicit or auto-detected)
- Resolve input into domain models
- Execute downloads
- Emit progress events in a provider-agnostic way
"""

from __future__ import annotations

from typing import Iterable, List, Optional

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

    def __init__(self, *, progress: Optional[ProgressCallback] = None) -> None:
        self._progress = progress

    def run(self, request: PipelineRequest) -> List[CoreDownloadResult]:
        """
        Execute a full pipeline request.

        Returns one DownloadResult per downloaded Track.
        """
        provider = self._select_provider(request)

        resolved = provider.resolve(
            request.source,
            progress=self._progress,
        )

        options = DownloadOptions(
            output_dir=request.output_dir,
            audio_format=request.audio_format,
            audio_quality=request.audio_quality,
            overwrite=request.overwrite,
            cookies_path=request.cookies_path,
            ffmpeg_path=request.ffmpeg_path,
            tmp_dir=request.tmp_dir,
        )

        results: List[CoreDownloadResult] = []

        if isinstance(resolved, Track):
            results.append(self._download_track(provider, resolved, options))
        elif isinstance(resolved, Collection):
            for item in resolved.entries:
                if isinstance(item, Track):
                    results.append(self._download_track(provider, item, options))
                else:
                    # Nested collections are allowed but flattened for now
                    results.extend(self._download_collection(provider, item, options))
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

    def _download_track(
        self,
        provider,
        track: Track,
        options: DownloadOptions,
    ) -> CoreDownloadResult:
        raw_result = provider.download(
            track,
            options,
            progress=self._progress,
        )

        files = [
            DownloadedFile(path=p)
            for p in raw_result.output_paths
        ]

        return CoreDownloadResult(
            provider_id=raw_result.provider_id,
            item_title=raw_result.item_title,
            files=files,
            warnings=list(raw_result.warnings),
        )

    def _download_collection(
        self,
        provider,
        collection: Collection,
        options: DownloadOptions,
    ) -> List[CoreDownloadResult]:
        results: List[CoreDownloadResult] = []
        for item in collection.entries:
            if isinstance(item, Track):
                results.append(self._download_track(provider, item, options))
            elif isinstance(item, Collection):
                results.extend(self._download_collection(provider, item, options))
        return results
