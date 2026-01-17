from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field


# Load .env if present (safe no-op if missing)
load_dotenv()


def _expand_path(p: Optional[str]) -> Optional[Path]:
    if not p:
        return None
    return Path(os.path.expandvars(os.path.expanduser(p)))


def _parse_bool(v: Optional[str]) -> Optional[bool]:
    if v is None:
        return None
    s = v.strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return None


def _load_yaml_if_available(config_file: Optional[Path]) -> dict:
    """
    YAML is optional: if PyYAML isn't installed, we just skip YAML configs
    instead of crashing at import time.
    """
    if not config_file or not config_file.exists():
        return {}

    try:
        import yaml  # type: ignore
    except Exception:
        # PyYAML not installed; ignore YAML config
        return {}

    try:
        with config_file.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


class AppSettings(BaseModel):
    # Paths
    download_dir: Path = Field(default_factory=lambda: Path.home() / "Downloads" / "AudioDL")
    tmp_dir: Optional[Path] = None

    # External tools
    yt_dlp_path: str = "yt-dlp"
    ffmpeg_path: Optional[str] = None
    cookies_path: Optional[Path] = None

    # Audio defaults
    audio_format: str = "mp3"
    audio_quality: str = "0"

    # Behaviour
    overwrite: bool = False

    # Advanced defaults (used by UI/pipeline if it wants)
    use_archive: bool = True
    archive_path: Optional[Path] = None

    loudnorm: bool = False
    embed_thumbnail: bool = False
    parse_metadata_artist_title: bool = True
    strip_emojis: bool = False

    model_config = {"extra": "forbid", "frozen": True}


def load_settings(*, config_file: Optional[Path] = None, overrides: Optional[dict] = None) -> AppSettings:
    """
    Precedence: overrides > env vars > yaml config > defaults
    """
    data: dict = {}

    # YAML config (optional; safe if PyYAML missing)
    data.update(_load_yaml_if_available(config_file))

    # ENV (AUDIODL_*)
    env = {
        "download_dir": os.getenv("AUDIODL_DOWNLOAD_DIR"),
        "tmp_dir": os.getenv("AUDIODL_TMP_DIR"),
        "yt_dlp_path": os.getenv("AUDIODL_YT_DLP_PATH"),
        "ffmpeg_path": os.getenv("AUDIODL_FFMPEG_PATH"),
        "cookies_path": os.getenv("AUDIODL_COOKIES_PATH"),
        "audio_format": os.getenv("AUDIODL_AUDIO_FORMAT"),
        "audio_quality": os.getenv("AUDIODL_AUDIO_QUALITY"),
        "overwrite": os.getenv("AUDIODL_OVERWRITE"),
        # advanced
        "use_archive": os.getenv("AUDIODL_USE_ARCHIVE"),
        "archive_path": os.getenv("AUDIODL_ARCHIVE_PATH"),
        "loudnorm": os.getenv("AUDIODL_LOUDNORM"),
        "embed_thumbnail": os.getenv("AUDIODL_EMBED_THUMBNAIL"),
        "parse_metadata_artist_title": os.getenv("AUDIODL_PARSE_METADATA"),
        "strip_emojis": os.getenv("AUDIODL_STRIP_EMOJIS"),
    }

    for k, v in env.items():
        if v is None or v == "":
            continue

        if k in {"download_dir", "tmp_dir", "cookies_path"}:
            data[k] = _expand_path(v)

        elif k in {"archive_path"}:
            data[k] = _expand_path(v)

        elif k in {
            "overwrite",
            "use_archive",
            "loudnorm",
            "embed_thumbnail",
            "parse_metadata_artist_title",
            "strip_emojis",
        }:
            b = _parse_bool(v)
            if b is not None:
                data[k] = b

        else:
            data[k] = v

    # Explicit overrides
    if overrides:
        data.update(overrides)

    return AppSettings(**data)
