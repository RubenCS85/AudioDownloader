from __future__ import annotations

import re
from typing import Optional
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


_SPOTIFY_HOSTS = {
    "open.spotify.com",
    "play.spotify.com",
}

_SPOTIFY_PATH_RE = re.compile(r"^/(track|album|playlist)/([a-zA-Z0-9]+)")


def _is_spotify_url(source: str) -> bool:
    try:
        p = urlparse(source.strip())
        if p.scheme not in ("http", "https"):
            return False
        if (p.netloc or "").lower() not in _SPOTIFY_HOSTS:
            return False
        return bool(_SPOTIFY_PATH_RE.match(p.path))
    except Exception:
        return False


class SpotifyProvider:
    """
    Spotify provider (stub).

    Design notes:
    - Spotify does NOT allow direct audio downloads.
    - Real implementation will:
        1) Resolve metadata via Spotify Web API
        2) Map tracks to a downloadable source (e.g. YouTube search)
        3) Delegate actual download to another provider (bridge pattern)
    """

    @property
    def id(self) -> str:
        return "spotify"

    @property
    def display_name(self) -> str:
        return "Spotify"

    def can_handle(self, source: str) -> bool:
        return _is_spotify_url(source)

    def resolve(
        self,
        source: str,
        *,
        progress: Optional[ProgressCallback] = None,
    ) -> Track | Collection:
        emit_progress(
            progress,
            provider_id=self.id,
            phase="resolve",
            message="Resolviendo Spotify URL (no implementado)",
            progress_value=0.0,
        )

        raise ProviderError(
            "Spotify provider aún no implementado.\n"
            "Plan: usar Spotify Web API para metadatos y delegar descarga a YouTube."
        )

    def download(
        self,
        item: Track | Collection,
        options: DownloadOptions,
        *,
        progress: Optional[ProgressCallback] = None,
    ) -> DownloadResult:
        raise ProviderError(
            "Spotify no permite descarga directa de audio.\n"
            "Este provider funcionará como resolver + delegador en el futuro."
        )


# Register at import time
register_provider(SpotifyProvider())
