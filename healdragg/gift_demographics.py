"""Приблизительные категории имён для фильтров NFT, не факты о людях."""
from __future__ import annotations

from functools import lru_cache

from .intel import guess


@lru_cache(maxsize=32768)
def name_categories(label: str | None) -> tuple[str, str]:
    label = (label or "").strip()
    # Метки «id 123», @username и пустые имена — не имена людей.
    if not label or label.startswith("@") or label.lower().startswith("id "):
        return "unknown", "unknown"
    name = label.split()[0]
    if not any(ch.isalpha() for ch in name):
        return "unknown", "unknown"
    result = guess(name, online=False)
    # Не распределяем по одной лишь последней букве произвольного ника.
    if result.source not in ("dictionary", "script"):
        return "unknown", "unknown"
    return result.gender if result.gender in ("male", "female") else "unknown", result.country or "unknown"
