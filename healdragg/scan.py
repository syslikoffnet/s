"""Сбор каталога и проверка участия цели.

Два независимых сценария:

``bootstrap``
    Наполняет базу чатов: диалоги каждой сессии + глобальный поиск по словам
    (и, если попросить, вступление в найденные публичные чаты — иначе проверять
    участие цели будет не в чем).

``target``
    Проверяет, в каких чатах каталога состоит цель. Если среди сессий есть
    сессия самой цели — просто читаем её диалоги (это самый точный и быстрый
    путь), иначе идём по каталогу через ``channels.getParticipant`` и
    глобальный поиск сообщений цели.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from telethon import functions, types
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import Channel, Chat

from .alerts import run_detection
from .config import Settings
from .db import GROUP_FIELDS, Store
from .tg import (
    Account,
    RateLimiter,
    SessionPool,
    chat_to_group,
    fetch_history_meta,
    find_participant,
    history_peer,
    join_public_group,
    resolve_target,
)
from .util import now_ts

log = logging.getLogger("healdragg.scan")


def group_kwargs(info: dict[str, Any]) -> dict[str, Any]:
    """Оставляет только колонки ``groups`` — защита от лишних ключей."""
    return {key: value for key, value in info.items() if key in GROUP_FIELDS}

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

DEFAULT_QUERIES = (
    "чат",
    "общение",
    "флуд",
    "мемы",
    "музыка",
    "игры",
    "знакомства",
    "крипта",
    "работа",
    "аниме",
)


# ── отчётность ─────────────────────────────────────────────────────────────
class Reporter:
    """Пишет прогресс в БД (для TUI/бота/веба) и зовёт колбэк."""

    def __init__(
        self,
        store: Store,
        *,
        scan_id: int | None = None,
        callback: ProgressCallback | None = None,
        min_interval: float = 1.2,
    ) -> None:
        self.store = store
        self.scan_id = scan_id
        self.callback = callback
        self.min_interval = min_interval
        self._last = 0.0
        self._last_done = -1
        self.state: dict[str, Any] = {"phase": "старт", "done": 0, "total": 0, "found": 0}
        self.errors = 0

    async def update(self, *, force: bool = False, **data: Any) -> None:
        self.state.update(data)
        now = time.monotonic()
        if not force and now - self._last < self.min_interval and self.state["done"] == self._last_done:
            return
        self._last = now
        self._last_done = int(self.state.get("done") or 0)
        if self.scan_id:
            self.store.update_scan(
                self.scan_id,
                done=self._last_done,
                total=int(self.state.get("total") or 0),
                found=int(self.state.get("found") or 0),
            )
        if self.callback:
            result = self.callback(dict(self.state))
            if asyncio.iscoroutine(result):
                await result

    async def event(self, message: str, level: str = "info") -> None:
        self.store.log_event(level, message, "scan")
        if level == "error":
            self.errors += 1
        if self.callback:
            result = self.callback({**self.state, "message": message, "level": level})
            if asyncio.iscoroutine(result):
                await result


# ── сбор каталога ──────────────────────────────────────────────────────────
async def sync_dialogs(
    pool: SessionPool,
    store: Store,
    session_name: str,
    *,
    reporter: Reporter | None = None,
    fetch_meta: bool = True,
    meta_limit: int = 500,
) -> dict[str, int]:
    """Сохраняет все чаты из диалогов сессии."""
    account = await pool.connect(session_name)
    session_row = store.get_session(session_name)
    session_pk = int(session_row["id"]) if session_row else 0
    limiter = account.limiter or RateLimiter()

    stats = {"checked": 0, "groups": 0, "private": 0, "meta": 0, "errors": 0}
    pending_meta: list[tuple[dict[str, Any], Any]] = []
    to_count = 0

    try:
        # wait_time ограничивает частоту подгрузки страниц диалогов
        async for dialog in account.client.iter_dialogs(limit=None, wait_time=1.0):
            entity = dialog.entity
            if not isinstance(entity, (Channel, Chat)):
                continue
            info = chat_to_group(entity)
            if not info:
                continue
            if dialog.message is not None and getattr(dialog.message, "date", None):
                info["last_message_at"] = int(dialog.message.date.timestamp())
            info["discovered_from"] = "dialogs"
            info["discovered_by"] = session_name
            group_pk = store.upsert_group(**group_kwargs(info))
            store.link_session_group(
                session_pk,
                group_pk,
                is_member=True,
                access_hash=info.get("access_hash"),
            )
            stats["groups"] += 1
            stats["checked"] += 1
            if not info.get("is_public"):
                stats["private"] += 1
            if fetch_meta and to_count < meta_limit:
                group_row = store.get_group(info["tg_id"])
                if group_row and not _meta_fresh(group_row, store):
                    pending_meta.append((group_row, entity))
                    to_count += 1
            if reporter and stats["checked"] % 25 == 0:
                await reporter.update(
                    phase=f"диалоги · {session_name}",
                    done=stats["checked"],
                    total=max(stats["checked"], stats["checked"]),
                    found=stats["groups"],
                )
    except RPCError as exc:
        stats["errors"] += 1
        if reporter:
            await reporter.event(f"{session_name}: диалоги — {type(exc).__name__}", "error")
        log.warning("iter_dialogs %s: %s", session_name, exc)

    for group_row, entity in pending_meta:
        try:
            total, last = await fetch_history_meta(account.client, entity, limiter)
            store.set_group_meta(
                int(group_row["tg_id"]),
                messages_total=total,
                last_message_at=last,
            )
            stats["meta"] += 1
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            log.debug("meta %s: %s", group_row.get("tg_id"), exc)

    if reporter:
        await reporter.update(force=True, phase=f"диалоги · {session_name}", done=stats["checked"], total=stats["checked"], found=stats["groups"])
    return stats


def _meta_fresh(group_row: dict[str, Any], store: Store, ttl_hours: float | None = None) -> bool:
    checked = int(group_row.get("meta_checked_at") or 0)
    if not checked:
        return False
    ttl = ttl_hours if ttl_hours is not None else 12.0
    return checked > now_ts() - int(ttl * 3600)


async def search_public(
    pool: SessionPool,
    store: Store,
    session_name: str,
    queries: Sequence[str],
    *,
    reporter: Reporter | None = None,
    limit: int = 50,
) -> dict[str, int]:
    """Глобальный поиск публичных чатов по ключевым словам."""
    account = await pool.connect(session_name)
    limiter = account.limiter or RateLimiter()
    stats = {"checked": 0, "groups": 0, "public": 0, "errors": 0}
    session_row = store.get_session(session_name)
    session_pk = int(session_row["id"]) if session_row else 0

    for query in queries:
        try:
            result = await limiter.call(
                lambda q=query: account.client(
                    functions.contacts.SearchRequest(q=q, limit=limit)
                )
            )
        except RPCError as exc:
            stats["errors"] += 1
            log.warning("search %r: %s", query, exc)
            continue
        for entity in getattr(result, "chats", None) or []:
            info = chat_to_group(entity)
            if not info:
                continue
            stats["checked"] += 1
            if not info.get("is_public"):
                continue
            info["discovered_from"] = "search"
            info["discovered_by"] = session_name
            group_pk = store.upsert_group(**group_kwargs(info))
            if session_pk:
                store.link_session_group(session_pk, group_pk, is_member=False)
            stats["groups"] += 1
            stats["public"] += 1
        if reporter:
            await reporter.update(
                force=True,
                phase=f"поиск · {query}",
                done=stats["checked"],
                total=stats["checked"],
                found=stats["groups"],
            )
    return stats


async def join_discovered(
    pool: SessionPool,
    store: Store,
    session_name: str,
    *,
    limit: int | None = None,
    reporter: Reporter | None = None,
) -> dict[str, int]:
    """Вступает в найденные, но ещё не покрытые публичные чаты.

    Аккуратно: дневной лимит на сессию, пауза между вступлениями, только
    публичные чаты по username. Нужно для проверки участия цели там, где мы
    не состоим.
    """
    account = await pool.connect(session_name)
    limiter = account.limiter or RateLimiter()
    session_row = store.get_session(session_name)
    session_pk = int(session_row["id"]) if session_row else 0
    limit = 20 if limit is None else limit
    candidates = store.query(
        """
        SELECT g.* FROM groups g
         WHERE g.is_public = 1 AND g.username IS NOT NULL
           AND NOT EXISTS (
                SELECT 1 FROM session_groups sg
                 WHERE sg.group_id = g.id AND sg.session_id = ? AND sg.is_member = 1
           )
         ORDER BY coalesce(g.members_count, 0) DESC, coalesce(g.last_message_at, 0) DESC
         LIMIT ?
        """,
        (session_pk, max(0, int(limit))),
    )
    stats = {"checked": len(candidates), "joined": 0, "errors": 0, "skipped": 0}
    daily_key = f"join:{session_name}"
    for group in candidates:
        if limit and store.daily_count(daily_key) >= int(limit):
            stats["skipped"] += 1
            store.log_event(
                "warn", f"{session_name}: дневной лимит вступлений ({limit}) исчерпан", "scan"
            )
            break
        try:
            ok = await join_public_group(account.client, group, limiter)
        except FloodWaitError:
            raise
        if ok:
            stats["joined"] += 1
            store.incr_daily(daily_key)
            store.link_session_group(
                session_pk, int(group["id"]), is_member=True, access_hash=group.get("access_hash")
            )
        else:
            stats["errors"] += 1
        if reporter:
            await reporter.update(force=True, phase="вступления", done=stats["joined"], total=len(candidates), found=stats["joined"])
    if reporter and candidates:
        await reporter.event(f"{session_name}: вступил в {stats['joined']} чатов")
    return stats


async def bootstrap(
    pool: SessionPool,
    store: Store,
    settings: Settings,
    *,
    sessions: Sequence[str] | None = None,
    queries: Sequence[str] | None = None,
    join: bool = False,
    join_limit: int | None = None,
    fetch_meta: bool = True,
    reporter: Reporter | ProgressCallback | None = None,
) -> dict[str, Any]:
    """Полный проход: диалоги всех сессий → поиск по словам → (опц.) вступления."""
    names = list(sessions or store.session_names())
    if not names:
        raise RuntimeError("Нет сессий. Сначала: healdragg session add main")

    scan_id = store.start_scan("bootstrap", sessions=names)
    if isinstance(reporter, Reporter):
        reporter.scan_id = scan_id
    else:
        reporter = Reporter(store, scan_id=scan_id, callback=reporter)
    started = time.time()
    totals = {"checked": 0, "groups": 0, "private": 0, "joined": 0, "errors": 0}

    try:
        for name in names:
            await reporter.update(force=True, phase=f"диалоги · {name}", done=totals["checked"], total=0)
            try:
                stats = await sync_dialogs(
                    pool, store, name, reporter=reporter, fetch_meta=fetch_meta
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("sync_dialogs %s: %s", name, exc)
                await reporter.event(f"{name}: {exc}", "error")
                continue
            totals["checked"] += stats["checked"]
            totals["groups"] += stats["groups"]
            totals["private"] += stats["private"]
            totals["errors"] += stats["errors"]

        queries = [q for q in (queries or settings.search_queries or DEFAULT_QUERIES) if q]
        if queries:
            for name in names:
                try:
                    stats = await search_public(pool, store, name, queries, reporter=reporter)
                    totals["groups"] += stats["groups"]
                except Exception as exc:  # noqa: BLE001
                    log.warning("search_public %s: %s", name, exc)

        if join and (join_limit is None or join_limit > 0):
            for name in names:
                limit = join_limit if join_limit is not None else settings.join_limit
                try:
                    stats = await join_discovered(pool, store, name, limit=limit, reporter=reporter)
                    totals["joined"] += stats["joined"]
                except FloodWaitError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.warning("join %s: %s", name, exc)

        elapsed = time.time() - started
        totals["elapsed"] = round(elapsed, 1)
        store.finish_scan(scan_id, status="done", stats=totals)
        await reporter.update(force=True, phase="готово", found=totals["groups"])
        store.log_event(
            "info",
            f"база пополнена: +{totals['groups']} чатов за {elapsed:.0f} с",
            "bootstrap",
        )
        return {"scan_id": scan_id, **totals}
    except asyncio.CancelledError:
        store.finish_scan(scan_id, status="cancelled", stats=totals)
        raise
    except Exception as exc:  # noqa: BLE001
        store.finish_scan(scan_id, status="error", stats=totals, error=str(exc))
        raise


# ── проверка цели ──────────────────────────────────────────────────────────
async def scan_own_dialogs(
    pool: SessionPool,
    store: Store,
    target: dict[str, Any],
    session_name: str,
    *,
    reporter: Reporter | None = None,
) -> dict[str, int]:
    """Если сессия принадлежит цели — её диалоги и есть ответ."""
    account = await pool.connect(session_name)
    if account.user_id != int(target["tg_user_id"]):
        raise RuntimeError("эта сессия не принадлежит цели")

    limiter = account.limiter or RateLimiter()
    stats = {"checked": 0, "found": 0, "meta": 0, "errors": 0}
    target_pk = int(target["id"])
    session_row = store.get_session(session_name)
    session_pk = int(session_row["id"]) if session_row else 0
    seen_group_pks: list[int] = []
    dialogs: list[Any] = []
    async for dialog in account.client.iter_dialogs(limit=None, wait_time=1.0):
        if isinstance(dialog.entity, (Channel, Chat)):
            dialogs.append(dialog)

    total = len(dialogs)
    for dialog in dialogs:
        entity = dialog.entity
        info = chat_to_group(entity)
        if not info:
            continue
        entity_creator = bool(getattr(entity, "creator", False))
        entity_admin = bool(getattr(entity, "creator", False) or getattr(entity, "admin_rights", None))
        status = "creator" if entity_creator else ("admin" if entity_admin else "member")
        if dialog.message is not None and getattr(dialog.message, "date", None):
            info["last_message_at"] = int(dialog.message.date.timestamp())
        info["discovered_from"] = "own_session"
        info["discovered_by"] = session_name
        group_pk = store.upsert_group(**group_kwargs(info))
        if session_pk:
            store.link_session_group(
                session_pk, group_pk, is_member=True, access_hash=info.get("access_hash")
            )
        store.upsert_target_group(
            target_pk, group_pk, status=status, source="own_session", session=session_name
        )
        seen_group_pks.append(group_pk)
        stats["found"] += 1
        stats["checked"] += 1

        group_row = store.get_group(info["tg_id"])
        if group_row and not _meta_fresh(group_row, store):
            try:
                count, last = await fetch_history_meta(account.client, entity, limiter)
                store.set_group_meta(
                    int(group_row["tg_id"]), messages_total=count, last_message_at=last
                )
                stats["meta"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                log.debug("meta %s: %s", info["tg_id"], exc)
        if reporter and stats["checked"] % 5 == 0:
            await reporter.update(
                phase="диалоги цели",
                done=stats["checked"],
                total=total,
                found=stats["found"],
            )

    # чего больше нет в диалогах — помечаем «вышел» (как в оригинальном списке)
    stats["left"] = store.mark_target_groups_left(target_pk, seen_group_pks, source="own_session")

    if reporter:
        await reporter.update(force=True, phase="диалоги цели", done=stats["checked"], total=total, found=stats["found"])
    return stats


async def probe_target(
    pool: SessionPool,
    store: Store,
    settings: Settings,
    target: dict[str, Any],
    *,
    sessions: Sequence[str] | None = None,
    max_groups: int | None = None,
    reporter: Reporter | None = None,
    deep: bool = False,
) -> dict[str, int]:
    """Проверяет участие цели в чатах каталога через getParticipant."""
    names = list(sessions or store.session_names())
    coverage = store.session_group_map(names)
    if not coverage:
        raise RuntimeError("Нет покрытых чатов: сначала собери базу (bootstrap)")

    # какая сессия может проверить какой чат
    per_group: dict[int, list[str]] = {}
    for session_name, groups in coverage.items():
        for tg_id in groups:
            per_group.setdefault(int(tg_id), []).append(session_name)

    rows = store.groups_by_tg_ids(per_group.keys())
    rows.sort(key=lambda g: (g.get("last_message_at") or 0, g.get("members_count") or 0), reverse=True)
    if max_groups:
        rows = rows[: int(max_groups)]

    target_pk = int(target["id"])
    existing = {int(r["tg_id"]) for r in store.all_target_groups(target_pk)}
    stats = {"checked": 0, "found": 0, "meta": 0, "errors": 0, "skipped": 0}
    load = dict.fromkeys(names, 0)
    limiter_cache: dict[str, RateLimiter] = {}
    total = len(rows)

    for group in rows:
        candidates = per_group.get(int(group["tg_id"])) or []
        if not candidates:
            stats["skipped"] += 1
            continue
        # наименее загруженная сессия, которая точно состоит в чате
        session_name = min(candidates, key=lambda n: load.get(n, 0))
        load[session_name] = load.get(session_name, 0) + 1
        try:
            account: Account = await pool.connect(session_name)
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            log.warning("сессия %s: %s", session_name, exc)
            load[session_name] = 10**6
            continue
        limiter = limiter_cache.get(session_name) or account.limiter or RateLimiter()
        limiter_cache[session_name] = limiter
        session_hash = coverage.get(session_name, {}).get(int(group["tg_id"]))

        try:
            status, target_hash = await find_participant(
                account.client,
                group,
                target,
                limiter,
                session_access_hash=session_hash,
                deep_pages=3 if deep else 0,
            )
        except FloodWaitError:
            raise
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            log.debug("probe %s: %s", group.get("tg_id"), exc)
            status, target_hash = None, None

        if target_hash and not target.get("access_hash"):
            target = {**target, "access_hash": int(target_hash)}
            store.upsert_target(
                int(target["tg_user_id"]), access_hash=int(target_hash)
            )

        if status:
            store.upsert_target_group(
                target_pk,
                int(group["id"]),
                status=status,
                source="probe",
                session=session_name,
            )
            stats["found"] += 1
            if not _meta_fresh(group, store, settings.meta_ttl_hours):
                try:
                    count, last = await fetch_history_meta(
                        account.client, history_peer(group, session_hash), limiter
                    )
                    store.set_group_meta(int(group["tg_id"]), messages_total=count, last_message_at=last)
                    stats["meta"] += 1
                except Exception:  # noqa: BLE001
                    pass
        elif int(group["tg_id"]) in existing and group.get("kind") != "channel":
            # в broadcast-канале подписчики не считаются участниками:
            # getParticipant для них отвечает UserNotParticipant, это не «вышел»
            store.mark_target_group(target_pk, int(group["id"]), "left")

        stats["checked"] += 1
        if reporter:
            await reporter.update(
                phase="проверка участия",
                done=stats["checked"],
                total=total,
                found=stats["found"],
            )

    if reporter:
        await reporter.update(force=True, phase="проверка участия", done=stats["checked"], total=total, found=stats["found"])
    store.touch_target_scan(int(target["tg_user_id"]))
    return stats


async def global_search_target(
    pool: SessionPool,
    store: Store,
    target: dict[str, Any],
    *,
    sessions: Sequence[str] | None = None,
    reporter: Reporter | None = None,
    limit: int = 100,
) -> dict[str, int]:
    """Ищет сообщения цели по всем чатам и добирает найденные чаты в базу.

    Работает не на всех аккаунтах/слоях (сервер может ответить ошибкой) —
    поэтому весь блок обёрнут и не считается критичным.
    """
    names = list(sessions or store.session_names())
    target_pk = int(target["id"])
    target_peer = None
    stats = {"checked": 0, "found": 0, "channels": 0, "errors": 0}
    from .tg import target_input_peer

    target_peer = target_input_peer(target)
    for session_name in names:
        try:
            account = await pool.connect(session_name)
        except Exception:  # noqa: BLE001
            continue
        limiter = account.limiter or RateLimiter()
        client = account.client
        try:
            result = await limiter.call(
                lambda client=client: client(
                    functions.messages.SearchRequest(
                        peer=types.InputPeerEmpty(),
                        q="",
                        filter=types.InputMessagesFilterEmpty(),
                        min_date=None,
                        max_date=None,
                        offset_id=0,
                        add_offset=0,
                        limit=limit,
                        max_id=0,
                        min_id=0,
                        hash=0,
                        from_id=target_peer,
                    )
                ),
                attempts=1,
            )
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            log.info("global search %s недоступен: %s", session_name, exc)
            continue
        # Список chats — лишь связанные сущности ответа, не доказательство авторства.
        # Только явный from_id пользователя подтверждает наблюдение в конкретном чате.
        observed: set[tuple[str, int]] = set()
        target_id = int(target["tg_user_id"])
        for message in getattr(result, "messages", None) or []:
            author = getattr(message, "sender_id", None) or getattr(getattr(message, "from_id", None), "user_id", None)
            if author != target_id:
                continue
            peer = getattr(message, "peer_id", None)
            if isinstance(peer, types.PeerChannel):
                observed.add(("channel", int(peer.channel_id)))
            elif isinstance(peer, types.PeerChat):
                observed.add(("chat", int(peer.chat_id)))
        chats = getattr(result, "chats", None) or []
        for entity in chats:
            if isinstance(entity, types.Channel):
                key = ("channel", int(entity.id))
            elif isinstance(entity, types.Chat):
                key = ("chat", int(entity.id))
            else:
                continue
            if key not in observed:
                continue
            info = chat_to_group(entity)
            if not info:
                continue
            info["discovered_from"] = "global_search"
            info["discovered_by"] = session_name
            group_pk = store.upsert_group(**group_kwargs(info))
            # Даже авторство не доказывает текущее членство, роль или владение каналом.
            is_channel = info.get("kind") == "channel"
            store.record_observed_chat(target_pk, group_pk, source="global_search")
            stats["found"] += 1
            stats["channels"] += int(is_channel)
            stats["checked"] += 1
            group_row = store.get_group(info["tg_id"])
            if group_row and not _meta_fresh(group_row, store):
                try:
                    count, last = await fetch_history_meta(
                        account.client,
                        history_peer(group_row, info.get("access_hash")),
                        limiter,
                    )
                    store.set_group_meta(
                        int(info["tg_id"]), messages_total=count, last_message_at=last
                    )
                except Exception:  # noqa: BLE001
                    pass
        if reporter:
            await reporter.update(force=True, phase="глобальный поиск", done=stats["checked"], found=stats["found"])
    return stats


async def scan_target(
    pool: SessionPool,
    store: Store,
    settings: Settings,
    target: dict[str, Any],
    *,
    sessions: Sequence[str] | None = None,
    max_groups: int | None = None,
    deep: bool = False,
    reporter: ProgressCallback | None = None,
    use_global_search: bool = True,
) -> dict[str, Any]:
    """Полный прогон по цели. Сам выбирает лучший доступный способ."""
    names = list(sessions or store.session_names())
    if not names:
        raise RuntimeError("Нет сессий: healdragg session add main")

    target_pk = int(target["id"])
    scan_id = store.start_scan("probe", target_pk=target_pk, sessions=names)
    rep = Reporter(store, scan_id=scan_id, callback=reporter)
    started = time.time()
    totals: dict[str, Any] = {"checked": 0, "found": 0, "errors": 0, "mode": ""}

    try:
        # 1. Если у цели есть своя сессия — это самый точный источник
        own_sessions = [
            s["name"]
            for s in store.list_sessions()
            if s.get("tg_user_id") == int(target["tg_user_id"]) and s.get("status") == "active"
        ]
        if own_sessions:
            totals["mode"] = "own_session"
            for name in own_sessions:
                stats = await scan_own_dialogs(pool, store, target, name, reporter=rep)
                totals.update({k: totals.get(k, 0) + stats.get(k, 0) for k in stats})
        else:
            totals["mode"] = "probe"
            try:
                stats = await probe_target(
                    pool,
                    store,
                    settings,
                    target,
                    sessions=names,
                    max_groups=max_groups,
                    reporter=rep,
                    deep=deep,
                )
                totals.update({k: totals.get(k, 0) + stats.get(k, 0) for k in stats})
            except RuntimeError as exc:
                await rep.event(str(exc), "error")
            if use_global_search:
                try:
                    stats = await global_search_target(
                        pool, store, target, sessions=names, reporter=rep
                    )
                    totals["global_search"] = stats
                except Exception as exc:  # noqa: BLE001
                    log.debug("global search: %s", exc)

        store.touch_target_scan(int(target["tg_user_id"]))
        created = run_detection(store, target, since=int(started))
        totals["alerts"] = len(created)
        totals["elapsed"] = round(time.time() - started, 1)
        final = store.target_stats(int(target["id"]))
        totals["total_in_base"] = final.get("total", 0)
        store.finish_scan(
            scan_id,
            status="done",
            stats={"mode": totals["mode"], **{k: v for k, v in totals.items() if k != "mode"}},
        )
        await rep.update(force=True, phase="готово", found=final.get("total", 0))
        store.log_event(
            "info",
            f"цель {target['tg_user_id']}: +{totals.get('found', 0)} чатов "
            f"({totals['mode']}, {totals['elapsed']} с)",
            "target",
        )
        return {"scan_id": scan_id, **totals}
    except asyncio.CancelledError:
        store.finish_scan(scan_id, status="cancelled", stats=totals)
        raise
    except Exception as exc:  # noqa: BLE001
        store.finish_scan(scan_id, status="error", stats=totals, error=str(exc))
        raise


async def resolve_and_store_target(
    pool: SessionPool,
    store: Store,
    raw: str,
    *,
    watch_enabled: bool = True,
) -> dict[str, Any]:
    """Разрешает @username / id и кладёт профиль в базу."""
    account = await pool.first()
    limiter = account.limiter or RateLimiter()
    data = await resolve_target(account.client, raw, limiter)
    if not data.get("tg_user_id"):
        raise ValueError("не удалось определить пользователя")
    return store.upsert_target(
        int(data["tg_user_id"]),
        access_hash=data.get("access_hash"),
        username=data.get("username"),
        first_name=data.get("first_name"),
        last_name=data.get("last_name"),
        phone=data.get("phone"),
        bio=data.get("bio"),
        is_bot=bool(data.get("is_bot")),
        is_deleted=bool(data.get("is_deleted")),
        watch_enabled=watch_enabled,
    )

