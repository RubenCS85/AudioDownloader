"""
Logging setup for audiodl.

- Uses standard logging so it works everywhere (CLI/UI/tests).
- Optional RichHandler for nicer CLI output if 'rich' is installed (it is in deps).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

try:
    from rich.logging import RichHandler  # type: ignore
except Exception:  # pragma: no cover
    RichHandler = None  # type: ignore


def setup_logging(
    *,
    level: Optional[str] = None,
    use_rich: bool = True,
) -> None:
    """
    Configure root logging.

    Precedence for level:
      1) explicit `level` argument
      2) env var AUDIODL_LOG_LEVEL
      3) default INFO
    """
    chosen = (level or os.getenv("AUDIODL_LOG_LEVEL") or "INFO").upper()

    root = logging.getLogger()
    # Avoid duplicated handlers if setup_logging is called multiple times (UI reloads/tests)
    if root.handlers:
        root.setLevel(chosen)
        return

    handlers = []
    if use_rich and RichHandler is not None:
        handlers.append(
            RichHandler(
                rich_tracebacks=True,
                show_time=True,
                show_level=True,
                show_path=False,
            )
        )
        logging.basicConfig(
            level=chosen,
            format="%(message)s",
            datefmt="[%X]",
            handlers=handlers,
        )
    else:
        logging.basicConfig(
            level=chosen,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        )

    # Quiet down overly chatty libs (tweak as needed)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Convenience wrapper to keep logging imports consistent.
    """
    return logging.getLogger(name)
