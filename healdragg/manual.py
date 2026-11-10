"""Ручное пополнение каталога: ссылки, @username и «тихие» вступления.

Пользователь вставляет список вида::

    https://t.me/memachh
    @beeeelogvardeyskaya
    t.me/+AbCdEfGhIjK
    -1001234567890

Модуль разбирает строки локально (быстро и без сети), показывает, что не
похоже на чат, а затем по кнопке «Добавить» проверяет каждую запись через
Telegram, кладёт чаты в базу и по желанию вступает — сразу в архив и без
уведомлений.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from telethon import functions, types
from telethon.errors import (
    ChannelPrivateError,
    ChannelsTooMuchError,
    FloodWaitError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    InviteRequestSentError,
    RPCError,
    UserAlreadyParticipantError,
    UserNotParticipantError,
)
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest

from .db import GROUP_FIELDS, Store
from .tg import RateLimiter, SessionPool, chat_to_group, fetch_history_meta, history_peer

log = logging.getLogger("healdragg.manual")

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
INVITE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# Служебные пути t.me — это не чаты, их нельзя добавлять в базу.
RESERVED = {
    "addstickers",
    "addemoji",
    "addtheme",
    "share",
    "proxy",
    "socks",
    "iv",
    "login",
    "confirmphone",
    "setlanguage",
    "contact",
    "joinchat",
    "c",
    "s",
    "bg",
    "invoice",
}

# Символы, которые обычно прилипают к ссылке при копировании из чата.
_TRIM = " \t\r\n<>\"'«»„“()[]{}.,;:!?"""


def _clean(raw: str) -> str:
    return raw.strip().strip(_TRIM).strip()


def _parse_path(path: str) -> tuple[str, str] | None:
    path = path.strip("/")
    if not path:
        return None
    parts = path.split("/")
    head = parts[0]
    low = head.lower()
    if low.startswith("+") and INVITE_RE.match(head[1:]):
        return "invite", head[1:]
    if low == "joinchat" and len(parts) > 1 and INVITE_RE.match(parts[1]):
        return "invite", parts[1]
    if low == "s" and len(parts) > 1:
        return _parse_path("/".join(parts[1:]))
    if low == "c" and len(parts) > 1 and parts[1].isdigit():
        # t.me/c/1234567890/1 → -1001234567890
        return "id", f"-100{parts[1]}"
    if low in RESERVED:
        return None
    if USERNAME_RE.match(head):
        return "username", head
    return None


def parse_token(raw: str) -> tuple[str, str] | None:
    """Одна запись → (вид, значение). ``None`` — не похоже на чат."""
    text = _clean(raw)
    if not text:
        return None
    low = text.lower()

    if low.startswith("tg://"):
        query = parse_qs(urlparse(text).query)
        domain = (query.get("domain") or [""])[0]
        invite = (query.get("invite") or [""])[0]
        if invite and INVITE_RE.match(invite):
            return "invite", invite
        if domain and USERNAME_RE.match(domain):
            return "username", domain
        return None

    for prefix in ("https://", "http://"):
        if low.startswith(prefix):
            text = text[len(prefix) :]
            low = text.lower()
            break
    for host in ("www.t.me/", "t.me/", "telegram.me/", "telegram.dog/", "www.telegram.me/"):
        if low.startswith(host):
            parsed = _parse_path(text[len(host) :])
            if parsed:
                return parsed
            return None

    if text.startswith("@"):
        name = text[1:]
        return ("username", name) if USERNAME_RE.match(name) else None

    if re.fullmatch(r"-?\d{5,16}", text):
        return "id", text.lstrip("+")

    if USERNAME_RE.match(text):
        return "username", text
    return None


def parse_batch(text: str) -> dict[str, Any]:
    """Разбирает вставленный список без сети: элементы, дубли и ошибки."""
    items: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    total = 0

    for chunk in re.split(r"[\s,;]+", text or ""):
        raw = _clean(chunk)
        if not raw:
            continue
        total += 1
        parsed = parse_token(raw)
        if parsed is None:
            invalid.append({"raw": raw, "reason": "не ссылка и не @username"})
            continue
        kind, value = parsed
        key = (kind, value.lower())
        if key in seen:
            duplicates.append({"raw": raw, "kind": kind, "value": value})
            continue
        seen.add(key)
        items.append(
            {
                "raw": raw,
                "kind": kind,
                "value": value,
                "link": (
                    f"https://t.me/{value}"
                    if kind == "username"
                    else f"https://t.me/+{value}"
                    if kind == "invite"
                    else None
                ),
            }
        )

    valid = len(items)
    invalid_percent = round(100.0 * len(invalid) / total, 1) if total else 0.0
    return {
        "items": items,
        "invalid": invalid,
        "duplicates": duplicates,
        "stats": {
            "total": total,
            "valid": valid,
            "invalid": len(invalid),
            "duplicates": len(duplicates),
            "invalid_percent": invalid_percent,
            "usernames": sum(1 for item in items if item["kind"] == "username"),
            "invites": sum(1 for item in items if item["kind"] == "invite"),
            "ids": sum(1 for item in items if item["kind"] == "id"),
        },
    }


# ── проверка через Telegram ────────────────────────────────────────────────
async def _silence(client: Any, entity: Any, limiter: RateLimiter) -> None:
    """Архив + без звука: вступление не должно ничего дёргать в телефоне."""
    try:
        peer = await client.get_input_entity(entity)
        await client(
            functions.folders.EditPeerFoldersRequest(
                folder_peers=[types.InputFolderPeer(peer=peer, folder_id=1)]
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("archive %s: %s", entity, exc)
    try:
        peer = await client.get_input_entity(entity)
        await client(
            functions.account.UpdateNotifySettingsRequest(
                peer=types.InputNotifyPeer(peer=peer),
                settings=types.InputPeerNotifySettings(
                    show_previews=False,
                    silent=True,
                    mute_until=2147483647,
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("mute %s: %s", entity, exc)


async def _live_membership(client: Any, entity: Any, limiter: RateLimiter) -> bool | None:
    """Спрашивает у Telegram, состоим ли мы в чате. ``None`` — не знаем."""
    try:
        permissions = await limiter.call(lambda: client.get_permissions(entity))
        return permissions is not None
    except UserNotParticipantError:
        return False
    except (ChannelPrivateError, RPCError):
        return None


def _membership(entity: Any, member_ids: set[int]) -> bool:
    """Участие определяем по кэшу диалогов и флагам сущности.

    ``left``/``join_request`` надёжно означают «мы не внутри», остальное
    (например, только что найденный публичный чат) считаем неучастием, пока
    диалоги не перечитаны.
    """
    tg_id = int(getattr(entity, "id", 0) or 0)
    if getattr(entity, "left", False) or getattr(entity, "join_request", False):
        return False
    return tg_id in member_ids


async def _resolve_id(client: Any, value: str, limiter: RateLimiter) -> Any:
    raw = int(value)
    if raw < 0:
        internal = -raw - 1_000_000_000_000
        if internal <= 0:
            raise ValueError("некорректный id канала")
        peer = types.PeerChannel(internal)
        return await limiter.call(lambda: client.get_entity(peer))
    return await limiter.call(lambda: client.get_entity(raw))


async def _join(
    client: Any,
    *,
    entity: Any,
    invite: str | None,
    username: str | None,
    limiter: RateLimiter,
) -> tuple[Any, str]:
    """Возвращает (сущность, статус), где статус — joined/requested/error."""
    try:
        if invite:
            updates = await limiter.call(lambda: client(ImportChatInviteRequest(invite)))
            chats = getattr(updates, "chats", None) or []
            return (chats[0] if chats else entity, "joined")
        if username:
            target = await limiter.call(lambda: client.get_entity(username))
            await limiter.call(
                lambda: client(functions.channels.JoinChannelRequest(channel=target))
            )
            return target, "joined"
        return entity, "error"
    except InviteRequestSentError:
        return entity, "requested"
    except UserAlreadyParticipantError:
        return entity, "joined"
    except (InviteHashInvalidError, InviteHashExpiredError):
        return entity, "invite_invalid"
    except ChannelsTooMuchError:
        return entity, "too_many"
    except FloodWaitError:
        raise
    except RPCError as exc:
        log.info("join failed: %s", exc)
        return entity, "error"


async def import_groups(
    pool: SessionPool,
    store: Store,
    text: str,
    *,
    session: str | None = None,
    join: bool = False,
    archive: bool = True,
    reporter: Any = None,
) -> dict[str, Any]:
    """Проверяет записи через Telegram и кладёт чаты в базу.

    ``join=True`` дополнительно вступает в публичные чаты и по инвайт-ссылкам;
    после вступления чат уходит в архив и глушится (``archive=True``).
    """
    parsed = parse_batch(text)
    names = store.session_names()
    if not names:
        raise RuntimeError("Нет аккаунта-сканера — войди в Telegram в приложении")
    name = session if session in names else names[0]
    account = await pool.connect(name)
    limiter = account.limiter or RateLimiter()
    client = account.client
    session_row = store.get_session(name)
    session_pk = int(session_row["id"]) if session_row else 0
    member_ids = {
        int(row["tg_id"]) for row in store.session_groups(name, members_only=True)
    }

    rows: list[dict[str, Any]] = [
        {**item, "status": "invalid", "reason": item["reason"]} for item in parsed["invalid"]
    ]
    stats = {
        "parsed": parsed["stats"]["total"],
        "valid": len(parsed["items"]),
        "invalid": len(parsed["invalid"]),
        "invalid_percent": parsed["stats"]["invalid_percent"],
        "added": 0,
        "existing": 0,
        "updated": 0,
        "joined": 0,
        "requested": 0,
        "need_join": 0,
        "private": 0,
        "channels": 0,
        "groups": 0,
        "errors": 0,
    }
    total = len(parsed["items"])
    if reporter and hasattr(reporter, "update"):
        await reporter.update(force=True, phase="проверка ссылок", done=0, total=total, found=0)

    for index, item in enumerate(parsed["items"], start=1):
        kind, value = item["kind"], item["value"]
        row: dict[str, Any] = {**item, "status": "error", "reason": None}
        invite: str | None = None
        try:
            entity: Any = None
            member = False
            if kind == "username":
                entity = await limiter.call(lambda value=value: client.get_entity(value))
            elif kind == "id":
                entity = await _resolve_id(client, value, limiter)
            else:
                invite = value
                invite_info = await limiter.call(lambda value=value: client(CheckChatInviteRequest(value)))
                if isinstance(invite_info, types.ChatInviteAlready):
                    entity = invite_info.chat
                    member = True
                elif isinstance(invite_info, types.ChatInvitePeek):
                    entity = invite_info.chat
                elif getattr(invite_info, "request_needed", False):
                    row["reason"] = "закрытый чат: нужна заявка на вступление"
                if entity is None and join:
                    entity, status = await _join(
                        client, entity=entity, invite=invite, username=None, limiter=limiter
                    )
                    if status == "requested":
                        stats["requested"] += 1
                        row.update(status="requested", reason="заявка отправлена")
                        rows.append(row)
                        continue
                    if status == "joined":
                        member = True
                    elif status != "joined":
                        row["reason"] = "вступить не удалось"
                if entity is None:
                    stats["need_join"] += 1
                    row.update(status="need_join", title=getattr(invite_info, "title", None))
                    rows.append(row)
                    if reporter and hasattr(reporter, "update"):
                        await reporter.update(
                            force=True, phase="проверка ссылок", done=index, total=total
                        )
                    continue

            if isinstance(entity, types.User):
                row["reason"] = "это пользователь, а не чат"
                stats["errors"] += 1
                rows.append(row)
                continue

            info = chat_to_group(entity)
            if not info:
                row["reason"] = "это не группа и не канал"
                stats["errors"] += 1
                rows.append(row)
                continue

            tg_id = int(info["tg_id"])
            exists = store.get_group(tg_id)
            username = info.get("username")
            if kind == "invite" or not username:
                info["link"] = item.get("link") or (f"https://t.me/+{invite}" if invite else None)
            info["discovered_from"] = "manual"
            info["discovered_by"] = name
            group_pk = store.upsert_group(
                **{key: val for key, val in info.items() if key in GROUP_FIELDS}
            )

            if not member:
                member = _membership(entity, member_ids)
            if not member:
                live = await _live_membership(client, entity, limiter)
                if live is not None:
                    member = live

            if join and not member:
                entity, status = await _join(
                    client,
                    entity=entity,
                    invite=invite,
                    username=username,
                    limiter=limiter,
                )
                if status == "joined":
                    member = True
                    stats["joined"] += 1
                    info = chat_to_group(entity) or info
                    tg_id = int(info["tg_id"])
                    group_pk = store.upsert_group(
                        **{key: val for key, val in info.items() if key in GROUP_FIELDS}
                    )
                    if archive:
                        await _silence(client, entity, limiter)
                elif status == "requested":
                    stats["requested"] += 1
                    row["reason"] = "заявка отправлена"
                elif status == "invite_invalid":
                    row["reason"] = "инвайт-ссылка устарела"
                elif status == "too_many":
                    row["reason"] = "в аккаунте слишком много чатов"
                else:
                    row["reason"] = "вступить не удалось"

            if session_pk:
                store.link_session_group(
                    session_pk,
                    group_pk,
                    is_member=member,
                    access_hash=info.get("access_hash"),
                )

            # Свежая активность: так чат встаёт в каталоге на своё место,
            # а карточка сразу показывает размер и дату последнего сообщения.
            if info.get("kind") in {"channel", "supergroup"}:
                try:
                    group_row = store.get_group(tg_id)
                    if group_row:
                        count, last = await fetch_history_meta(
                            client,
                            history_peer(group_row, info.get("access_hash")),
                            limiter,
                        )
                        if count is not None or last is not None:
                            store.set_group_meta(
                                tg_id, messages_total=count, last_message_at=last
                            )
                except Exception as exc:  # noqa: BLE001
                    log.debug("history meta %s: %s", tg_id, exc)

            is_channel = info.get("kind") == "channel"
            stats["channels"] += int(is_channel)
            stats["groups"] += int(not is_channel)
            stats["private"] += int(not info.get("username"))
            if exists:
                stats["existing"] += 1
            else:
                stats["added"] += 1
            if not member and not join:
                stats["need_join"] += 1

            row.update(
                status="joined" if member and join and not exists else ("existing" if exists else "added"),
                tg_id=tg_id,
                title=info.get("title"),
                username=info.get("username"),
                kind=info.get("kind"),
                link=info.get("link"),
                members_count=info.get("members_count"),
                member=member,
            )
        except FloodWaitError:
            row["status"] = "error"
            row["reason"] = "Telegram просит подождать (FloodWait)"
            stats["errors"] += 1
        except (InviteHashInvalidError, InviteHashExpiredError):
            row["status"] = "error"
            row["reason"] = "инвайт-ссылка недействительна"
            stats["errors"] += 1
        except (ValueError, TypeError) as exc:
            row["status"] = "error"
            row["reason"] = str(exc)
            stats["errors"] += 1
        except Exception as exc:  # noqa: BLE001
            log.info("manual import %s: %s", value, exc)
            row["status"] = "error"
            row["reason"] = "не удалось проверить: " + type(exc).__name__
            stats["errors"] += 1

        rows.append(row)
        if reporter and hasattr(reporter, "update"):
            await reporter.update(
                force=True,
                phase=f"проверка {index}/{total}",
                done=index,
                total=total,
                found=stats["added"],
            )

    stats["session"] = name
    if join:
        store.incr_daily(f"join:{name}:manual", stats["joined"])
    return {"stats": stats, "rows": rows[:500], "invalid": parsed["invalid"]}
