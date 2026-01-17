from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl


class ItemKind(str, Enum):
    track = "track"
    collection = "collection"  # playlist/album/set/etc.


class ProviderRef(BaseModel):
    """
    Points to the provider that can resolve/download this item.
    """

    id: str = Field(..., min_length=1, description="Stable provider id, e.g. 'youtube'")
    display_name: Optional[str] = Field(None, description="Human readable name")


class MediaBase(BaseModel):
    """
    Shared fields for Track/Collection.
    """

    provider: ProviderRef
    kind: ItemKind
    title: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1, description="Original input or canonical URL/ID")

    # Optional enriched metadata (best-effort; providers may omit)
    url: Optional[HttpUrl] = None
    duration_seconds: Optional[int] = Field(None, ge=0)
    thumbnail_url: Optional[HttpUrl] = None

    # Arbitrary provider-specific metadata (avoid putting logic in here)
    meta: Dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "extra": "forbid",
        "frozen": True,  # hashable/immutable; helps caching and safe sharing between threads
    }


class Track(MediaBase):
    """
    A single downloadable audio item.
    """

    kind: ItemKind = Field(default=ItemKind.track, frozen=True)

    artist: Optional[str] = None
    album: Optional[str] = None
    track_number: Optional[int] = Field(None, ge=1)
    year: Optional[int] = Field(None, ge=0)
    explicit: Optional[bool] = None


class Collection(MediaBase):
    """
    A playlist/album/set that contains tracks (or nested collections).
    """

    kind: ItemKind = Field(default=ItemKind.collection, frozen=True)

    entries: List["MediaItem"] = Field(default_factory=list)
    total: Optional[int] = Field(None, ge=0)

    model_config = {
        "extra": "forbid",
        "frozen": True,
    }


MediaItem = Track | Collection


class DownloadedFile(BaseModel):
    """
    One concrete file created by a provider/pipeline.
    """

    path: str
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = Field(None, ge=0)

    model_config = {
        "extra": "forbid",
        "frozen": True,
    }


class DownloadResult(BaseModel):
    """
    Outcome of downloading a single Track (or a single resolved element in a batch).
    """

    provider_id: str
    item_title: str
    files: List[DownloadedFile] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    model_config = {
        "extra": "forbid",
        "frozen": True,
    }


class PipelineRequest(BaseModel):
    """
    High-level request to the pipeline. UI/CLI build one of these.
    """

    source: str  # URL/ID/query
    output_dir: str
    provider_id: Optional[str] = None  # if user explicitly selects provider

    audio_format: str = "mp3"
    audio_quality: str = "0"
    overwrite: bool = False

    # --- Advanced options (providers may ignore if not supported) ---
    use_archive: bool = True
    archive_path: Optional[str] = None  # if None and use_archive=True -> pipeline will default it

    loudnorm: bool = False
    embed_thumbnail: bool = False

    parse_metadata_artist_title: bool = True  # "Artista - Título" -> artist/title mapping (best-effort)
    strip_emojis: bool = False

    cookies_path: Optional[str] = None
    ffmpeg_path: Optional[str] = None
    tmp_dir: Optional[str] = None

    model_config = {
        "extra": "forbid",
        "frozen": True,
    }
