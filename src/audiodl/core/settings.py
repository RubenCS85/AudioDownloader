from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


# Load .env if present (safe no-op if missing)
load_dotenv()


def _expand_path(p: Optional[str]) -> Optional[Path]:
    if not p:
        return None
    return Path(os.path.expandvars(os.path.expanduser(p)))


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

    model_config = {"extra": "forbid", "frozen": True}


def load_settings(*, config_file: Optional[Path] = None, overrides: Optional[dict] = None) -> AppSettings:
    """
    Precedence: overrides > env vars > yaml config > defaults
    """
    data: dict = {}

    # YAML config
    if config_file and config_file.exists():
        with config_file.open("r", encoding="utf-8") as f:
            data.update(yaml.safe_load(f) or {})

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
    }

    for k, v in env.items():
        if v is None or v == "":
            continue
        if k in {"download_dir", "tmp_dir", "cookies_path"}:
            data[k] = _expand_path(v)
        elif k == "overwrite":
            data[k] = v.strip().lower() in {"1", "true", "yes", "on"}
        else:
            data[k] = v

    # Explicit overrides
    if overrides:
        data.update(overrides)

    return AppSettings(**data)
