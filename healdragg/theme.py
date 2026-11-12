"""Единая палитра и консоль для CLI/TUI."""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

THEME = Theme(
    {
        "brand": "bold #ff79c6",
        "accent": "bold #8be9fd",
        "good": "bold #50fa7b",
        "warn": "bold #f1fa8c",
        "bad": "bold #ff5555",
        "muted": "#6272a4",
        "date": "#8be9fd",
        "title": "bold #f8f8f2",
        "count": "#bd93f9",
        "frame": "#44475a",
        "header": "#bd93f9",
    }
)


def get_console(*, stderr: bool = False) -> Console:
    return Console(theme=THEME, highlight=False, soft_wrap=False, stderr=stderr)
