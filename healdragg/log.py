"""Логи: красиво в терминале, по-человечески в файле."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

NOISY = ("telethon", "aiogram.event", "aiogram.dispatcher", "httpx", "urllib3")


def setup_logging(level: str = "INFO", log_file: Path | str | None = None) -> None:
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level.upper())
        return
    root.setLevel(level.upper())

    fmt_file = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    use_rich = sys.stderr.isatty() and os.getenv("NO_COLOR") is None
    stream_handler: logging.Handler
    if use_rich:
        try:
            from rich.logging import RichHandler

            stream_handler = RichHandler(rich_tracebacks=True, show_path=False, markup=False)
            stream_handler.setFormatter(logging.Formatter("%(message)s", "%H:%M:%S"))
        except Exception:  # noqa: BLE001
            stream_handler = logging.StreamHandler(sys.stderr)
            stream_handler.setFormatter(fmt_file)
    else:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(fmt_file)
    root.addHandler(stream_handler)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(fmt_file)
        root.addHandler(file_handler)

    for name in NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
