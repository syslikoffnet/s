"""Telegram-бот @healdragg_bot."""

from .app import run_bot  # noqa: F401
from .deps import Deps  # noqa: F401

__all__ = ["run_bot", "Deps"]
