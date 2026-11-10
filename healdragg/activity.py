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

from .alerts import run_detection
from .config import Settings
from .db import Store
from .scan import Reporter
from .tg import RateLimiter, SessionPool, history_peer, target_input_peer
from .util import now_ts

log = logging.getLogger("healdragg.activity")

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

MEDIA_LIMIT = 4000


def _media_kind(message: Any) -> str | None:
    """Тип вложения человеческим словом: гиф, фото, видео, стикер и т.д."""
    media = getattr(message, "media", None)
    if media is None:
        return None
    document = getattr(message, "document", None) or getattr(media, "document", None)
    if document is not None:
        names = {type(attr).__name__ for attr in getattr(document, "attributes", None) or []}
        if "DocumentAttributeAnimated" in names:
            return "gif"
        if "DocumentAttributeVideo" in names:
            return "video"
        if "DocumentAttributeAudio" in names:
            return "audio"
        if "DocumentAttributeSticker" in names:
            return "sticker"
        return "document"
    if getattr(message, "photo", None) is not None or type(media).__name__ == "MessageMediaPhoto":
        return "photo"
    name = type(media).__name__
    if name.startswith("MessageMedia"):
        return name[len("MessageMedia") :].lower() or "media"
    return name.lower()


async def _target_entity(client: Any, target: dict[str, Any], limiter: RateLimiter) -> Any:
    """Сущность цели для ``from_user``.

    ``InputPeerUser`` с нулевым access_hash ломает ``iter_messages``, поэтому
    сначала пробуем имя и числовой id через кэш самого клиента.
    """
    candidates: list[Any] = []
    username = target.get("username")
    if username:
        candidates.append(username)
    candidates.append(int(target["tg_user_id"]))
    for candidate in candidates:
        try:
            return await limiter.call(lambda candidate=candidate: client.get_entity(candidate))
        except Exception as exc:  # noqa: BLE001
            log.debug("target entity %s: %s", candidate, exc)
    return target_input_peer(target)


def _chat_candidates(store: Store, target_pk: int, limit: int) -> list[dict[str, Any]]:
    """Чаты, где могут быть сообщения цели.

    Сначала пересечения «цель × чат», затем — все чаты сканера: если скан не
    успел записать target_groups, общая группа всё равно найдётся.
    """
    chats = [g for g in store.all_target_groups(target_pk) if g.get("status") != "left"]
    if len(chats) >= limit:
        return chats[:limit]
    seen = {int(g.get("group_id") or g.get("id")) for g in chats}
    extra = store.query(
        """
        SELECT g.*, g.id AS group_id FROM session_groups sg
          JOIN groups g ON g.id = sg.group_id
         WHERE sg.is_member = 1
         GROUP BY g.id
         ORDER BY coalesce(g.last_message_at, 0) DESC, coalesce(g.members_count, 0) DESC
         LIMIT ?
        """,
        (max(1, int(limit) * 2),),
    )
    for row in extra:
        if int(row["group_id"]) in seen:
            continue
        chats.append(row)
        seen.add(int(row["group_id"]))
        if len(chats) >= limit:
            break
    return chats


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



def _record_observed_chat(store: Store, target_pk: int, group_pk: int, *, source: str) -> None:
    """Сообщение подтверждает связь с чатом, но не текущее членство.

    Не перезаписываем результаты проверок членства более слабым свидетельством.
    """
    ts = now_ts()
    with store.tx() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO target_groups
               (target_id, group_id, status, source, found_at, updated_at)
               VALUES (?, ?, 'unknown', ?, ?, ?)""",
            (target_pk, group_pk, source, ts, ts),
        )


async def _dialog_chats(
    pool: SessionPool,
    store: Store,
    target: dict[str, Any],
    coverage: dict[str, dict[int, int]],
    limit: int = 150,
) -> list[dict[str, Any]]:
    """Чаты из диалогов наших аккаунтов — работают без предварительного скана.

    Ключевой сценарий: цель и наш аккаунт сидят в одной группе, но участники
    скрыты и скан ничего не записал. Диалоги видны всегда, а сообщения цели мы
    фильтруем сами — membership для этого не нужен.
    """
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for name in store.session_names():
        try:
            account = await pool.connect(name)
        except Exception as exc:  # noqa: BLE001
            log.debug("dialog sweep: сессия %s: %s", name, exc)
            continue
        try:
            async for dialog in account.client.iter_dialogs(limit=max(20, int(limit))):
                entity = getattr(dialog, "entity", None)
                tg_id = int(getattr(entity, "id", 0) or 0)
                if not tg_id or not (dialog.is_group or dialog.is_channel):
                    continue
                access_hash = getattr(entity, "access_hash", None)
                coverage.setdefault(name, {})[tg_id] = access_hash
                if tg_id in seen:
                    continue
                seen.add(tg_id)
                megagroup = bool(getattr(entity, "megagroup", False))
                kind = "supergroup" if megagroup else ("channel" if dialog.is_channel else "group")
                group_pk = store.upsert_group(
                    tg_id,
                    title=str(getattr(entity, "title", "") or ""),
                    access_hash=access_hash,
                    username=getattr(entity, "username", None),
                    kind=kind,
                    is_megagroup=megagroup,
                    members_count=getattr(entity, "participants_count", None),
                    discovered_from="dialogs",
                    discovered_by=name,
                )
                out.append(
                    {
                        "group_id": group_pk,
                        "id": group_pk,
                        "tg_id": tg_id,
                        "title": str(getattr(entity, "title", "") or ""),
                        "kind": kind,
                        "access_hash": access_hash,
                        "status": "unknown",
                    }
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("dialog sweep %s: %s", name, exc)
    return out


async def _global_target_messages(
    pool: SessionPool,
    store: Store,
    target: dict[str, Any],
    cutoff: int,
    *,
    limit: int = 100,
) -> dict[str, int]:
    """Дополнительная попытка поиска сообщений через Telegram API.

    Доступность такого запроса зависит от Telegram и прав аккаунта.
    Не гарантирует поиск по чатам, недоступным аккаунту.
    """
    stats = {"found": 0, "chats": 0, "stored": 0}
    names = store.session_names()
    if not names:
        return stats
    from telethon.tl.functions.messages import SearchRequest
    from telethon.tl.types import InputMessagesFilterEmpty, InputPeerEmpty, InputUser

    target_tg_id = int(target["tg_user_id"])
    target_pk = int(target["id"])
    try:
        account = await pool.connect(names[0])
    except Exception as exc:  # noqa: BLE001
        log.warning("global search: сессия: %s", exc)
        return stats
    limiter = account.limiter or RateLimiter()
    try:
        entity = await limiter.call(
            lambda: account.client.get_entity(target.get("username") or target_tg_id)
        )
    except Exception as exc:  # noqa: BLE001
        log.info("global search: цель не резолвится (%s)", exc)
        return stats
    access_hash = getattr(entity, "access_hash", None)
    if not access_hash:
        return stats
    try:
        result = await limiter.call(
            lambda: account.client(
                SearchRequest(
                    peer=InputPeerEmpty(),
                    q="",
                    from_id=InputUser(target_tg_id, int(access_hash)),
                    filter=InputMessagesFilterEmpty(),
                    min_date=None,
                    max_date=None,
                    offset_id=0,
                    add_offset=0,
                    limit=max(10, min(200, int(limit))),
                    max_id=0,
                    min_id=0,
                    hash=0,
                )
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.info("global search недоступен: %s", exc)
        return stats

    chats = {int(c.id): c for c in getattr(result, "chats", []) or []}
    seen_chats: set[int] = set()
    for message in getattr(result, "messages", []) or []:
        if isinstance(message, types.MessageEmpty):
            continue
        peer = getattr(message, "peer_id", None)
        chat_id = None
        for attr in ("channel_id", "chat_id"):
            value = getattr(peer, attr, None)
            if value:
                chat_id = int(value)
                break
        if not chat_id or chat_id not in chats:
            continue
        chat = chats[chat_id]
        date = int(message.date.timestamp()) if getattr(message, "date", None) else 0
        if date and date < cutoff:
            continue
        seen_chats.add(chat_id)
        megagroup = bool(getattr(chat, "megagroup", False))
        kind = "supergroup" if megagroup else ("channel" if getattr(chat, "broadcast", False) else "group")
        group_pk = store.upsert_group(
            chat_id,
            title=str(getattr(chat, "title", "") or ""),
            access_hash=getattr(chat, "access_hash", None),
            username=getattr(chat, "username", None),
            kind=kind,
            is_megagroup=megagroup,
            members_count=getattr(chat, "participants_count", None),
            discovered_from="search",
        )
        _record_observed_chat(store, target_pk, group_pk, source="search")
        text = (getattr(message, "message", "") or "").strip()[:MEDIA_LIMIT]
        inserted = store.add_message(
            target_pk,
            group_pk,
            int(message.id),
            date=date or now_ts(),
            text=text,
            reply_to_msg=getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None),
            mentions=",".join(str(i) for i in _mention_ids(message)) or None,
            media=_media_kind(message),
            views=getattr(message, "views", None),
        )
        if inserted:
            for mention_id in _mention_ids(message):
                if mention_id != target_tg_id:
                    peer_pk = store.upsert_peer(mention_id)
                    store.bump_edge(target_pk, peer_pk, mentions=1, ts=date or now_ts())
        stats["found"] += 1
        stats["stored"] += int(inserted)
    stats["chats"] = len(seen_chats)
    return stats


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
    incremental: bool = False,
) -> dict[str, Any]:
    """Собирает последние сообщения цели и связи с собеседниками.

    ``incremental=True`` — быстрый добор для режима наблюдения: меньше чатов,
    с ограниченным разбором ответов и без чистки базы.
    """
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
    cutoff = now_ts() - max(1, int(days)) * 86400
    if incremental:
        per_chat = min(int(per_chat), 25)
        max_chats = min(int(max_chats), 40)
        max_replies = min(int(max_replies), 25)
        cutoff = now_ts() - 2 * 86400
    groups = _chat_candidates(store, target_pk, max(1, int(max_chats)))
    # Скан мог не успеть записать session_groups — тогда чаты берём прямо из
    # диалогов аккаунта. Без этого на экране цели было «0 групп».
    if len(groups) < max(1, int(max_chats)):
        known = {int(g.get("tg_id") or 0) for g in groups}
        extra = await _dialog_chats(
            pool,
            store,
            target,
            coverage,
            limit=250 if not incremental else 120,
        )
        for row in extra:
            if int(row["tg_id"]) in known:
                continue
            known.add(int(row["tg_id"]))
            groups.append(row)
            if len(groups) >= max(1, int(max_chats)):
                break

    stats: dict[str, Any] = {
        "chats": 0,
        "checked": 0,
        "messages": 0,
        "peers": 0,
        "replies": 0,
        "mentions": 0,
        "skipped": 0,
        "errors": 0,
        "global_found": 0,
        "global_chats": 0,
    }
    started = time.time()
    replies_left = max(0, int(max_replies))

    # Дополнительная попытка поиска; её доступность зависит от Telegram.
    try:
        global_stats = await _global_target_messages(
            pool, store, target, cutoff, limit=100 if not incremental else 60
        )
        stats["global_found"] = global_stats["found"]
        stats["global_chats"] = global_stats["chats"]
        stats["messages"] += global_stats["stored"]
    except Exception as exc:  # noqa: BLE001
        log.warning("глобальный поиск: %s", exc)
        stats["global_found"] = 0
        stats["global_chats"] = 0

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
            from_user = await _target_entity(account.client, target, limiter)
            stats["chats"] += 1
            await rep.update(
                phase=f"читаю · {group.get('title') or group['tg_id']}",
                done=index - 1,
                total=len(groups),
                found=stats["messages"],
            )
            group_pk = int(group.get("group_id") or group["id"])
            try:
                messages_found: list[Any] = []
                try:
                    async for message in account.client.iter_messages(
                        peer,
                        from_user=from_user,
                        limit=max(1, int(per_chat)),
                    ):
                        messages_found.append(message)
                except Exception as exc:  # noqa: BLE001
                    # access_hash мог устареть: добираем историю и фильтруем сами
                    log.info("from_user %s: %s — читаю историю целиком", target_tg_id, exc)
                    async for message in account.client.iter_messages(
                        peer, limit=max(30, int(per_chat) * 4)
                    ):
                        sender = getattr(message, "sender_id", None) or getattr(
                            getattr(message, "from_id", None), "user_id", None
                        )
                        if int(sender or 0) == target_tg_id:
                            messages_found.append(message)
                for message in messages_found:
                    date = int(message.date.timestamp()) if getattr(message, "date", None) else 0
                    if date and date < cutoff:
                        continue
                    _record_observed_chat(store, target_pk, group_pk, source="activity")
                    text = (getattr(message, "message", "") or "").strip()[:MEDIA_LIMIT]
                    reply_msg_id = getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)
                    reply_peer_id: int | None = None
                    reply_info: dict[str, Any] | None = None
                    if reply_msg_id and replies_left > 0:
                        replies_left -= 1
                        try:
                            replied = await limiter.call(lambda m=message: m.get_reply())
                            sender = getattr(replied, "sender", None) if replied else None
                            if sender is None and replied is not None and hasattr(replied, "get_sender"):
                                sender = await limiter.call(replied.get_sender)
                            info = _peer_from_sender(sender)
                            if info:
                                reply_info = info
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
                    reply_is_new = inserted
                    if not inserted and reply_peer_id:
                        with store.tx() as conn:
                            updated = conn.execute(
                                "UPDATE messages SET reply_to_peer = ? WHERE target_id = ? AND group_id = ? "
                                "AND tg_msg_id = ? AND reply_to_peer IS NULL",
                                (reply_peer_id, target_pk, group_pk, int(message.id)),
                            )
                            reply_is_new = updated.rowcount > 0
                    if reply_is_new and reply_peer_id:
                        peer_pk = store.upsert_peer(
                            reply_peer_id,
                            username=reply_info.get("username") if reply_info else None,
                            first_name=reply_info.get("first_name") if reply_info else None,
                            last_name=reply_info.get("last_name") if reply_info else None,
                        )
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

        if incremental:
            stats["pruned"] = 0
        else:
            stats["pruned"] = store.prune_activity(target_pk, days=max(int(days), 30) * 3)
        stats["alerts"] = len(run_detection(store, target, messages=True))
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
