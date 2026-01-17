"""
Development launcher for the Tkinter UI.

Usage:
    python scripts/dev_run_tkinter.py

This avoids installing the package while developing and keeps UI iteration fast.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root / src is on sys.path
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# ---------------------------------------------------------------------------
# Launch UI
# ---------------------------------------------------------------------------

from audiodl.ui.tkinter import run  # noqa: E402


def main() -> None:
    run()


if __name__ == "__main__":
    main()
