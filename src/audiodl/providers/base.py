"""
Base provider interface + registry.

Goal:
- Make it trivial to add new providers (youtube/spotify/apple_music/mixcloud...)
- Keep core pipeline decoupled from provider implementations
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple, Type, TypeVar, runtime_checkable

# ---------------------------
# Types (kept lightweight on purpose)
# ---------------------------

ProviderInput = str  # URL, ID, search query, etc. (provider decides)


@dataclass(frozen=True)
class ResolvedItem:
    """
    A normalized representation of "something to download" returned by a provider.

    This is intentionally minimal so we can evolve core models later without breaking providers.
    Providers should return either:
    - kind="track" with one track-like item
    - kind="collection" for playlists/albums/sets/etc + entries inside
    """
    provider_id: str
    kind: str  # "track" | "collection"
    title: str
    source: str  # original input (url/id/query) or canonical URL
    entries: Tuple["ResolvedItem", ...] = ()  # for collections


@dataclass(frozen=True)
class DownloadResult:
    """
    Minimal result object returned by providers.
    The pipeline can enrich this later (tags, archive, postprocess, etc.).
    """
    provider_id: str
    item_title: str
    output_paths: Tuple[str, ...]
    warnings: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DownloadOptions:
    """
    Provider-agnostic options.
    Providers may ignore unsupported fields (but should document it).
    """
    output_dir: str
    audio_format: str = "mp3"          # "mp3", "m4a", "wav"...
    audio_quality: str = "0"           # e.g. mp3 VBR 0, or provider-specific
    overwrite: bool = False
    cookies_path: Optional[str] = None
    ffmpeg_path: Optional[str] = None
    tmp_dir: Optional[str] = None


@dataclass(frozen=True)
class ProgressEvent:
    """
    Standard progress signal for UI/CLI.
    Providers should emit these through the progress callback.
    """
    provider_id: str
    phase: str                 # "resolve" | "download" | "postprocess"
    message: str
    progress: Optional[float] = None  # 0..1 if known


ProgressCallback = Callable[[ProgressEvent], None]


class ProviderError(RuntimeError):
    """Base exception raised by providers."""


# ---------------------------
# Provider interface
# ---------------------------

@runtime_checkable
class Provider(Protocol):
    """
    Providers implement:
    - can_handle(input): is this input for me?
    - resolve(input): return a ResolvedItem (track or collection)
    - download(resolved): perform download and return DownloadResult

    Design note:
    Providers SHOULD be stateless. Any state should be passed via options/context.
    """

    @property
    def id(self) -> str:
        """Stable identifier (e.g. 'youtube', 'spotify')."""

    @property
    def display_name(self) -> str:
        """Human readable name for UI."""

    def can_handle(self, source: ProviderInput) -> bool:
        """Return True if provider can handle given source (URL/ID/query)."""

    def resolve(
        self,
        source: ProviderInput,
        *,
        progress: Optional[ProgressCallback] = None,
    ) -> ResolvedItem:
        """Normalize source into a ResolvedItem structure."""

    def download(
        self,
        item: ResolvedItem,
        options: DownloadOptions,
        *,
        progress: Optional[ProgressCallback] = None,
    ) -> DownloadResult:
        """Download a resolved item and return filesystem paths."""


# ---------------------------
# Registry helpers
# ---------------------------

TProv = TypeVar("TProv", bound=Provider)

_PROVIDER_REGISTRY: Dict[str, Provider] = {}


def register_provider(provider: Provider) -> None:
    """
    Register a provider instance.

    Typical usage: call this in providers/<name>/provider.py at import time.
    """
    pid = provider.id.strip().lower()
    if not pid:
        raise ValueError("provider.id cannot be empty")
    if pid in _PROVIDER_REGISTRY:
        raise ValueError(f"Provider already registered: {pid}")
    _PROVIDER_REGISTRY[pid] = provider


def list_providers() -> Sequence[Provider]:
    """Return all registered providers."""
    return tuple(_PROVIDER_REGISTRY.values())


def get_provider(provider_id: str) -> Provider:
    pid = provider_id.strip().lower()
    try:
        return _PROVIDER_REGISTRY[pid]
    except KeyError as e:
        raise KeyError(f"Unknown provider_id '{provider_id}'. Registered: {sorted(_PROVIDER_REGISTRY)}") from e


def find_provider_for_source(source: ProviderInput) -> Provider:
    """
    Select a provider based on can_handle(). First match wins.
    If multiple providers could handle the input, order of registration matters.
    """
    for prov in _PROVIDER_REGISTRY.values():
        try:
            if prov.can_handle(source):
                return prov
        except Exception:
            # If a provider crashes in detection, don't break selection.
            continue
    raise ProviderError(
        "No provider can handle this input. "
        f"Registered providers: {', '.join(sorted(_PROVIDER_REGISTRY)) or '(none)'}"
    )


# ---------------------------
# Small utilities
# ---------------------------

def emit_progress(
    progress: Optional[ProgressCallback],
    *,
    provider_id: str,
    phase: str,
    message: str,
    progress_value: Optional[float] = None,
) -> None:
    if progress is None:
        return
    progress(ProgressEvent(provider_id=provider_id, phase=phase, message=message, progress=progress_value))
