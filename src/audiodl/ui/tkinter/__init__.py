"""
Tkinter UI package entry.

Exposes a single `run()` function so other entrypoints (scripts/CLI) can start the UI
without importing internal modules.
"""

from __future__ import annotations

from .app import run

__all__ = ["run"]
