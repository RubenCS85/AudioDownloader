"""
CLI entrypoint for audiodl.

This is intentionally minimal:
- No UI code
- No provider-specific logic
- Useful for testing the pipeline and for future automation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from audiodl.core.logging import setup_logging
from audiodl.core.models import PipelineRequest
from audiodl.core.pipeline import Pipeline
from audiodl.core.settings import load_settings
from audiodl.providers.base import ProgressEvent


def _print_progress(event: ProgressEvent) -> None:
    """
    Simple progress callback for CLI usage.
    """
    if event.progress is not None:
        pct = int(event.progress * 100)
        print(f"[{event.provider_id}][{event.phase}] {event.message} ({pct}%)")
    else:
        print(f"[{event.provider_id}][{event.phase}] {event.message}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audiodl",
        description="Extensible audio downloader (YouTube, Spotify, Apple Music, Mixcloud)",
    )

    parser.add_argument(
        "source",
        help="URL / ID / query to download",
    )

    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="Output directory for downloaded audio",
    )

    parser.add_argument(
        "--provider",
        help="Force provider id (e.g. youtube)",
    )

    parser.add_argument(
        "--format",
        default=None,
        help="Audio format (mp3, m4a, wav, ...)",
    )

    parser.add_argument(
        "--quality",
        default=None,
        help="Audio quality (provider-specific, e.g. mp3 V0 = 0)",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing files",
    )

    parser.add_argument(
        "--config",
        type=Path,
        help="Path to YAML config file",
    )

    parser.add_argument(
        "--log-level",
        default=None,
        help="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    setup_logging(level=args.log_level)

    settings = load_settings(config_file=args.config)

    request = PipelineRequest(
        source=args.source,
        output_dir=str(args.output_dir or settings.download_dir),
        provider_id=args.provider,
        audio_format=args.format or settings.audio_format,
        audio_quality=args.quality or settings.audio_quality,
        overwrite=args.overwrite or settings.overwrite,
        cookies_path=str(settings.cookies_path) if settings.cookies_path else None,
        ffmpeg_path=settings.ffmpeg_path,
        tmp_dir=str(settings.tmp_dir) if settings.tmp_dir else None,
    )

    pipeline = Pipeline(progress=_print_progress)

    try:
        results = pipeline.run(request)
    except Exception as exc:
        print(f"❌ Error: {exc}", file=sys.stderr)
        return 1

    print("\n✅ Descargas completadas:")
    for res in results:
        print(f"- {res.item_title}")
        for f in res.files:
            print(f"  → {f.path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
