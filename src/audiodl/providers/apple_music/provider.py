from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

from audiodl.core.models import Collection, Track
from audiodl.providers.base import (
    DownloadOptions,
    DownloadResult,
    ProgressCallback,
    ProviderError,
    emit_progress,
    register_provider,
)

_APPLE_HOSTS = {
    "music.apple.com",
}

# Examples:
# https://music.apple.com/es/album/.../id
# https://music.apple.com/es/song/.../id
# https://music.apple.com/es/playlist/.../id
# Sometimes IDs are embedded at the end.
_APPLE_PATH_RE = re.compile(r"^/([a-zA-Z]{2})/(song|album|playlist)/.+", re.IGNORECASE)


def _is_apple_music_url(source: str) -> bool:
    try:
        p = urlparse(source.strip())
        if p.scheme not in ("http", "https"):
            return False
        if (p.netloc or "").lower() not in _APPLE_HOSTS:
            return False
        return bool(_APPLE_PATH_RE.match(p.path or ""))
    except Exception:
        return False


class AppleMusicProvider:
    """
    Apple Music provider (stub).

    Design notes:
    - Apple Music access typically requires a Developer Token and (optionally) user token.
    - Direct audio download is not the normal/allowed path.
    - Real implementation will:
        1) Resolve metadata using Apple Music API (MusicKit)
        2) Map items to a downloadable source (e.g. YouTube search) or supported integrations
        3) Delegate actual download to another provider (bridge pattern)
    """

    @property
    def id(self) -> str:
        return "apple_music"

    @property
    def display_name(self) -> str:
        return "Apple Music"

    def can_handle(self, source: str) -> bool:
        return _is_apple_music_url(source)

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
            message="Resolviendo Apple Music URL (no implementado)",
            progress_value=0.0,
        )

        raise ProviderError(
            "Apple Music provider aún no implementado.\n"
            "Plan: usar Apple Music API para metadatos y delegar descarga a YouTube/u otro provider."
        )

    def download(
        self,
        item: Track | Collection,
        options: DownloadOptions,
        *,
        progress: Optional[ProgressCallback] = None,
    ) -> DownloadResult:
        raise ProviderError(
            "Apple Music no está pensado para descarga directa de audio.\n"
            "Este provider funcionará como resolver + delegador en el futuro."
        )


# Register at import time
register_provider(AppleMusicProvider())
