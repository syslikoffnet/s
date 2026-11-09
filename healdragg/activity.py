"""Что пишет цель и с кем взаимодействует.

Собираем только сообщения цели в уже известных чатах (публичные группы и
каналы, куда мы вступили или где сидит сама цель), из них строим агрегаты:

* ``messages`` — последние сообщения цели (для ленты «что пишет»);
* ``edges`` — «цель ↔ собеседник»: ответы и упоминания.

Тексты сообщений не покидают локальную базу; хранится ограниченное окно
(`ACTIVITY_DAYS`), лишнее вычищается через :meth:`Store.prune_activity`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from telethon import types
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import User

from .config import Settings
from .db import Store
from .scan import Reporter
from .tg import RateLimiter, SessionPool, history_peer, target_input_peer
from .util import now_ts

log = logging.getLogger("healdragg.activity")

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

MEDIA_LIMIT = 4000


def _media_kind(message: Any) -> str | None:
    media = getattr(message, "media", None)
    if media is None:
        return None
    name = type(media).__name__
    if name.startswith("MessageMedia"):
        return name[len("MessageMedia") :].lower() or "media"
    return name.lower()


def _mention_ids(message: Any) -> list[int]:
    out: list[int] = []
    for entity in getattr(message, "entities", None) or []:
        if isinstance(entity, types.MessageEntityMentionName):
            out.append(int(entity.user_id))
    return sorted(set(out))


def _peer_from_sender(sender: Any) -> dict[str, Any] | None:
    if not isinstance(sender, User):
        return None
    return {
        "tg_id": int(sender.id),
        "username": getattr(sender, "username", None),
        "first_name": getattr(sender, "first_name", None),
        "last_name": getattr(sender, "last_name", None),
    }


async def collect_activity(
    pool: SessionPool,
    store: Store,
    settings: Settings,
    target: dict[str, Any],
    *,
    days: int = 30,
    per_chat: int = 40,
    max_chats: int = 80,
    max_replies: int = 120,
    reporter: Reporter | ProgressCallback | None = None,
) -> dict[str, Any]:
    """Собирает последние сообщения цели и связи с собеседниками."""
    names = store.session_names()
    if not names:
        raise RuntimeError("Нет сессий: healdragg session add main")

    target_pk = int(target["id"])
    target_tg_id = int(target["tg_user_id"])
    scan_id = store.start_scan("activity", target_pk=target_pk, sessions=names)
    if isinstance(reporter, Reporter):
        reporter.scan_id = scan_id
        rep = reporter
    else:
        rep = Reporter(store, scan_id=scan_id, callback=reporter)

    coverage = store.session_group_map(names)
    groups = [g for g in store.all_target_groups(target_pk) if g.get("status") != "left"]
    groups = groups[: max(1, int(max_chats))]
    cutoff = now_ts() - max(1, int(days)) * 86400
    target_peer = target_input_peer(target)

    stats: dict[str, Any] = {
        "chats": 0,
        "checked": 0,
        "messages": 0,
        "peers": 0,
        "replies": 0,
        "mentions": 0,
        "skipped": 0,
        "errors": 0,
    }
    started = time.time()
    replies_left = max(0, int(max_replies))

    try:
        for index, group in enumerate(groups, start=1):
            candidates = [name for name, mapping in coverage.items() if int(group["tg_id"]) in mapping]
            if not candidates:
                stats["skipped"] += 1
                continue
            session_name = candidates[0]
            try:
                account = await pool.connect(session_name)
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                log.warning("activity: сессия %s: %s", session_name, exc)
                continue
            limiter = account.limiter or RateLimiter()
            access_hash = coverage.get(session_name, {}).get(int(group["tg_id"]))
            peer = history_peer(group, access_hash)
            stats["chats"] += 1
            await rep.update(
                phase=f"читаю · {group.get('title') or group['tg_id']}",
                done=index - 1,
                total=len(groups),
                found=stats["messages"],
            )
            group_pk = int(group.get("group_id") or group["id"])
            try:
                async for message in account.client.iter_messages(
                    peer,
                    from_user=target_peer,
                    limit=max(1, int(per_chat)),
                ):
                    date = int(message.date.timestamp()) if getattr(message, "date", None) else 0
                    if date and date < cutoff:
                        continue
                    text = (getattr(message, "message", "") or "").strip()[:MEDIA_LIMIT]
                    reply_msg_id = getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)
                    reply_peer_id: int | None = None
                    if reply_msg_id and replies_left > 0:
                        replies_left -= 1
                        try:
                            replied = await limiter.call(lambda m=message: m.get_reply())
                            sender = getattr(replied, "sender", None) if replied else None
                            if sender is None and replied is not None and hasattr(replied, "get_sender"):
                                sender = await limiter.call(replied.get_sender)
                            info = _peer_from_sender(sender)
                            if info:
                                reply_peer_id = info["tg_id"]
                        except (RPCError, OSError, asyncio.TimeoutError) as exc:  # noqa: BLE001
                            stats["errors"] += 1
                            log.debug("reply %s: %s", getattr(message, "id", "?"), exc)
                        except Exception as exc:  # noqa: BLE001
                            log.debug("reply %s: %s", getattr(message, "id", "?"), exc)

                    mention_ids = [i for i in _mention_ids(message) if i != target_tg_id]
                    inserted = store.add_message(
                        target_pk,
                        group_pk,
                        int(message.id),
                        date=date or now_ts(),
                        text=text,
                        reply_to_msg=reply_msg_id,
                        reply_to_peer=reply_peer_id,
                        mentions=",".join(str(i) for i in mention_ids) or None,
                        media=_media_kind(message),
                        views=getattr(message, "views", None),
                    )
                    if inserted and reply_peer_id:
                        peer_pk = store.upsert_peer(reply_peer_id)
                        store.bump_edge(target_pk, peer_pk, replies=1, ts=date or now_ts())
                        stats["replies"] += 1
                        stats["peers"] += 1
                    if inserted:
                        for mention_id in mention_ids:
                            peer_pk = store.upsert_peer(mention_id)
                            store.bump_edge(target_pk, peer_pk, mentions=1, ts=date or now_ts())
                            stats["mentions"] += 1
                            stats["peers"] += 1
                    stats["checked"] += 1
                    stats["messages"] += int(inserted)
            except FloodWaitError:
                raise
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                log.warning("activity %s: %s", group.get("title") or group["tg_id"], exc)

        pruned = store.prune_activity(target_pk, days=max(int(days), 30) * 3)
        stats["pruned"] = pruned
        stats["elapsed"] = round(time.time() - started, 1)
        store.finish_scan(scan_id, status="done", stats=stats)
        await rep.update(force=True, phase="готово", found=stats["messages"])
        store.log_event(
            "info",
            f"активность {target_tg_id}: {stats['messages']} сообщений, "
            f"{stats['peers']} собеседников",
            "activity",
        )
        return stats
    except asyncio.CancelledError:
        store.finish_scan(scan_id, status="cancelled", stats=stats)
        raise
    except Exception as exc:  # noqa: BLE001
        store.finish_scan(scan_id, status="error", stats=stats, error=str(exc))
        raise


__all__ = ["collect_activity"]
