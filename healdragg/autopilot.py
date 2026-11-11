"""Автопилот: сам сканирует цели, собирает активность и зажигает алерты.

Режимы запуска:

* ``healdragg api`` — фоновая задача, если ``AUTOPILOT_MINUTES > 0``;
* ``healdragg watch`` — отдельный консольный цикл (онли-консольный сценарий);
* ``healdragg bot`` — только push алертов (скан — на autopilot/API).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .activity import collect_activity
from .config import Settings
from .db import Store
from .scan import scan_target
from .tg import SessionPool

log = logging.getLogger("healdragg.autopilot")


async def tick(pool: SessionPool, store: Store, settings: Settings) -> dict[str, Any]:
    """Один обход всех целей: скан → (опц.) активность → алерты."""
    # Срез по первичному ключу не меняется от обновлений профилей во время скана.
    maximum = int(store.scalar("SELECT coalesce(max(id), 0) FROM targets", (), 0) or 0)
    target_count = store.count_targets()
    stats: dict[str, Any] = {
        "targets": target_count,
        "scanned": 0,
        "messages": 0,
        "alerts": 0,
        "errors": 0,
    }
    if not target_count:
        return stats
    if not store.session_names():
        stats["errors"] += 1
        log.warning("autopilot: нет сессий")
        return stats

    try:
        cursor = max(0, int(store.get_meta("autopilot_cursor") or 0))
    except ValueError:
        cursor = 0
    if cursor >= maximum:
        cursor = 0
    while cursor < maximum:
        targets = store.query("SELECT * FROM targets WHERE id > ? AND id <= ? ORDER BY id LIMIT 50",
                              (cursor, maximum))
        if not targets:
            break
        for target in targets:
            target_id = int(target["id"])
            try:
                result = await scan_target(pool, store, settings, target)
                stats["scanned"] += 1
                stats["alerts"] += int(result.get("alerts") or 0)
                if settings.auto_activity_on_scan:
                    activity = await collect_activity(
                        pool,
                        store,
                        settings,
                        target,
                        days=settings.activity_days,
                        per_chat=settings.activity_per_chat,
                        max_chats=settings.activity_max_chats,
                    )
                    stats["messages"] += int(activity.get("messages") or 0)
                    stats["alerts"] += int(activity.get("alerts") or 0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                log.warning("autopilot %s: %s", target.get("tg_user_id"), exc)
            cursor = target_id
            store.set_meta("autopilot_cursor", str(cursor))
    store.set_meta("autopilot_cursor", "0")
    if stats["alerts"]:
        store.log_event(
            "info",
            f"автопилот: {stats['scanned']} целей, {stats['alerts']} новых алертов",
            "autopilot",
        )
    stats["finished_at"] = int(time.time())
    return stats


async def loop(pool: SessionPool, store: Store, settings: Settings) -> None:
    """Бесконечный цикл; интервал в минутах (0 — выключен)."""
    minutes = max(1, int(settings.autopilot_minutes))
    while True:
        try:
            await tick(pool, store, settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("autopilot tick failed")
        await asyncio.sleep(minutes * 60)


__all__ = ["tick", "loop"]
