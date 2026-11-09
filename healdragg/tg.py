"""Telegram-слой: пул сессий, троттлинг и аккуратные запросы.

Правила, которые зашиты в код:

* все запросы идут через :class:`RateLimiter` (пауза + джиттер);
* ``FloodWait`` не игнорируется — спим ровно столько, сколько просят;
* ошибки одного чата не роняют прогон;
* access_hash хранится отдельно для каждой сессии (он привязан к аккаунту).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from telethon import TelegramClient, functions, types
from telethon.errors import (
    ChannelPrivateError,
    FloodWaitError,
    RPCError,
    UserNotParticipantError,
)
from telethon.tl.types import Channel, Chat, ChatForbidden, User

from .config import Settings
from .db import Store

log = logging.getLogger("healdragg.tg")

#: Эти ошибки детерминированы — повторять запрос бессмысленно и вредно.
NON_RETRYABLE = {
    "UserNotParticipantError",
    "ChannelPrivateError",
    "ChannelInvalidError",
    "UserIdInvalidError",
    "UserInvalidError",
    "ChatAdminRequiredError",
    "ChannelPublicGroupNaError",
    "PeerIdInvalidError",
}

T = Any


# ── троттлинг ──────────────────────────────────────────────────────────────
class RateLimiter:
    """Простейший «один запрос в N секунд» с джиттером."""

    def __init__(self, min_delay: float = 1.4, jitter: float = 0.6) -> None:
        self.min_delay = max(0.0, float(min_delay))
        self.jitter = max(0.0, float(jitter))
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self.min_delay + random.uniform(0, self.jitter)
            sleep_for = self._last + delay - now
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            self._last = time.monotonic()

    async def call(self, factory: Callable[[], Awaitable[T]], *, attempts: int = 3) -> T:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            await self.wait()
            try:
                return await factory()
            except FloodWaitError as exc:
                seconds = int(getattr(exc, "seconds", 0)) + random.uniform(1, 4)
                log.warning("FloodWait %.0f c — сплю", seconds)
                await asyncio.sleep(seconds)
                last_error = exc
            except (RPCError, OSError, asyncio.TimeoutError) as exc:
                if type(exc).__name__ in NON_RETRYABLE:
                    raise
                last_error = exc
                if attempt == attempts:
                    break
                await asyncio.sleep(1.5 * attempt + random.random())
        assert last_error is not None
        raise last_error


# ── описание сессии ────────────────────────────────────────────────────────
@dataclass(slots=True)
class Account:
    name: str
    client: TelegramClient
    user_id: int | None = None
    username: str | None = None
    phone: str | None = None
    groups: dict[int, Any] = field(default_factory=dict)  # tg_id -> access_hash
    limiter: RateLimiter | None = None
    error: str | None = None

    @property
    def label(self) -> str:
        if self.username:
            return f"{self.name} · @{self.username}"
        if self.user_id:
            return f"{self.name} · {self.user_id}"
        return self.name


class SessionPool:
    """Держит подключённые клиенты и выдаёт их по имени."""

    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store
        self._accounts: dict[str, Account] = {}
        self._connect_lock = asyncio.Lock()
        self._limiters: dict[str, RateLimiter] = {}

    # ── подключение ─────────────────────────────────────────────────────────
    async def connect(self, name: str, *, require_auth: bool = True) -> Account:
        async with self._connect_lock:
            account = self._accounts.get(name)
            if account and account.client.is_connected():
                if account.client.is_user_authorized() or not require_auth:
                    return account

            api_id, api_hash = self.settings.require_api()
            session_path = self.settings.session_file(name)
            session_path.parent.mkdir(parents=True, exist_ok=True)
            client = TelegramClient(
                str(session_path),
                api_id,
                api_hash,
                device_model="Healdragg",
                system_version="Android 14",
                app_version="1.0.0",
                lang_code="ru",
                system_lang_code="ru",
                connection_retries=5,
                retry_delay=2,
                request_retries=3,
                auto_reconnect=True,
                flood_sleep_threshold=15,
            )
            await client.connect()
            if require_auth and not await client.is_user_authorized():
                await client.disconnect()
                self.store.set_session_status(name, "needs_login", "сессия не авторизована")
                raise RuntimeError(
                    f"Сессия «{name}» не авторизована. Запусти: healdragg session add {name} --relogin"
                )

            me = None
            user_id = username = phone = None
            try:
                me = await client.get_me()
                if me:
                    user_id = me.id
                    username = getattr(me, "username", None)
                    phone = getattr(me, "phone", None)
            except RPCError:
                pass

            account = Account(
                name=name,
                client=client,
                user_id=user_id,
                username=username,
                phone=phone,
                limiter=self._limiters.setdefault(
                    name, RateLimiter(self.settings.scan_min_delay, self.settings.scan_jitter)
                ),
            )
            self._accounts[name] = account
            self.store.add_session(
                name,
                str(session_path),
                tg_user_id=user_id,
                username=username,
                first_name=getattr(me, "first_name", None) if me else None,
                phone=phone,
                status="active",
            )
            self.store.touch_session(name)
            return account

    async def account(self, name: str) -> Account:
        return await self.connect(name)

    async def connect_many(self, names: Sequence[str] | None = None) -> list[Account]:
        names = list(names or self.store.session_names())
        out: list[Account] = []
        for name in names:
            try:
                out.append(await self.connect(name))
            except Exception as exc:  # noqa: BLE001 — одна битая сессия не должна ронять пул
                log.warning("сессия %s: %s", name, exc)
                self.store.log_event("error", f"сессия {name}: {exc}", "pool")
        return out

    async def first(self, names: Sequence[str] | None = None) -> Account:
        accounts = await self.connect_many(names)
        if not accounts:
            raise RuntimeError("Нет доступных сессий. Добавь: healdragg session add main")
        return accounts[0]

    def connected(self) -> list[Account]:
        return [a for a in self._accounts.values() if a.client.is_connected()]

    async def close(self) -> None:
        for account in list(self._accounts.values()):
            try:
                await account.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._accounts.clear()

    def __contains__(self, name: str) -> bool:
        return name in self._accounts

    # ── удобные обёртки ─────────────────────────────────────────────────────
    async def call(
        self,
        name: str,
        factory: Callable[[TelegramClient], Awaitable[T]],
        *,
        attempts: int = 3,
    ) -> T:
        account = await self.connect(name)
        assert account.limiter is not None

        async def attempt() -> T:
            if not account.client.is_connected():
                await account.client.connect()
            return await factory(account.client)

        return await account.limiter.call(attempt, attempts=attempts)

    async def refresh_groups(self, account: Account) -> dict[int, Any]:
        mapping: dict[int, Any] = {}
        for row in self.store.session_groups(account.name, members_only=True):
            mapping[int(row["tg_id"])] = row.get("session_access_hash") or row.get("access_hash")
        account.groups = mapping
        return mapping


# ── конвертация сущностей ──────────────────────────────────────────────────
def user_to_dict(user: Any) -> dict[str, Any]:
    if user is None:
        return {}
    return {
        "tg_user_id": int(getattr(user, "id", 0)),
        "access_hash": getattr(user, "access_hash", None),
        "username": getattr(user, "username", None),
        "first_name": getattr(user, "first_name", None),
        "last_name": getattr(user, "last_name", None),
        "phone": getattr(user, "phone", None),
        "is_bot": bool(getattr(user, "bot", False)),
        "is_deleted": bool(getattr(user, "deleted", False)),
    }


def chat_to_group(entity: Any) -> dict[str, Any] | None:
    """Приводит Channel/Chat к плоскому словарю для БД."""
    if entity is None or isinstance(entity, (ChatForbidden,)):
        return None
    if isinstance(entity, Chat):
        return {
            "tg_id": int(entity.id),
            "access_hash": None,
            "title": entity.title or "",
            "username": getattr(entity, "username", None),
            "kind": "group",
            "is_public": bool(getattr(entity, "username", None)),
            "is_megagroup": False,
            "members_count": getattr(entity, "participants_count", None),
            "creator": bool(getattr(entity, "creator", False)),
            "admin": bool(getattr(entity, "creator", False) or getattr(entity, "admin_rights", None)),
        }
    if isinstance(entity, Channel):
        username = getattr(entity, "username", None)
        return {
            "tg_id": int(entity.id),
            "access_hash": getattr(entity, "access_hash", None),
            "title": entity.title or "",
            "username": username,
            "kind": "supergroup" if entity.megagroup else "channel",
            "is_public": bool(username),
            "is_megagroup": bool(entity.megagroup),
            "members_count": getattr(entity, "participants_count", None),
            "creator": bool(getattr(entity, "creator", False)),
            "admin": bool(getattr(entity, "creator", False) or getattr(entity, "admin_rights", None)),
        }
    if isinstance(entity, User):
        return None
    # иногда Telethon отдаёт «голые» объекты из поиска
    if hasattr(entity, "title") and hasattr(entity, "id"):
        username = getattr(entity, "username", None)
        return {
            "tg_id": int(entity.id),
            "access_hash": getattr(entity, "access_hash", None),
            "title": entity.title or "",
            "username": username,
            "kind": "supergroup" if getattr(entity, "megagroup", False) else "group",
            "is_public": bool(username),
            "is_megagroup": bool(getattr(entity, "megagroup", False)),
            "members_count": getattr(entity, "participants_count", None),
            "creator": False,
            "admin": False,
        }
    return None


def participant_status(participant: Any) -> str:
    name = type(participant).__name__.lower()
    if "creator" in name:
        return "creator"
    if "admin" in name:
        return "admin"
    if "banned" in name or "kicked" in name:
        return "left"
    return "member"


# ── разрешение цели ────────────────────────────────────────────────────────
def normalize_identifier(raw: str) -> str:
    text = (raw or "").strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "telegram.me/"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
    text = text.split("?")[0].strip("/")
    if text.startswith("@"):
        text = text[1:]
    return text


async def resolve_target(client: TelegramClient, raw: str, limiter: RateLimiter | None = None) -> dict[str, Any]:
    """Ищет пользователя по @username, ссылке или цифровому id.

    Цифровой id без access_hash работает только если аккаунт уже «видел» цель.
    Поэтому результат может быть без имени — для проверок этого достаточно,
    а хеш подтянется при первом же поиске по участникам.
    """
    ident = normalize_identifier(raw)
    if not ident:
        raise ValueError("Пустой идентификатор")

    limiter = limiter or RateLimiter(0.4, 0.2)
    if ident.isdigit():
        user_id = int(ident)
        user = None
        for attempt in (lambda: client.get_entity(user_id),):
            try:
                user = await limiter.call(attempt)
            except Exception:  # noqa: BLE001
                user = None
        if user is None:
            try:
                got = await limiter.call(
                    lambda: client(functions.users.GetUsersRequest([types.InputUser(user_id, 0)]))
                )
                user = got[0] if got else None
            except Exception:  # noqa: BLE001
                user = None
        data = user_to_dict(user) if isinstance(user, User) else {}
        data["tg_user_id"] = int(data.get("tg_user_id") or user_id)
        if isinstance(user, User):
            data["bio"] = await _fetch_bio(client, user, limiter)
        return data

    entity = await limiter.call(lambda: client.get_entity(ident))
    if isinstance(entity, User):
        data = user_to_dict(entity)
        data["bio"] = await _fetch_bio(client, entity, limiter)
        return data
    raise ValueError(f"«{raw}» — это не пользователь, а чат")


async def _fetch_bio(client: TelegramClient, user: User, limiter: RateLimiter) -> str | None:
    try:
        full = await limiter.call(
            lambda: client(functions.users.GetFullUserRequest(id=user))
        )
        return getattr(getattr(full, "full_user", None), "about", None)
    except Exception:  # noqa: BLE001
        return None


def target_input_peer(target: dict[str, Any]) -> Any:
    tg_id = int(target["tg_user_id"])
    access_hash = target.get("access_hash")
    if access_hash:
        return types.InputPeerUser(user_id=tg_id, access_hash=int(access_hash))
    return types.InputPeerUser(user_id=tg_id, access_hash=0)


def channel_peer(group: dict[str, Any], access_hash: Any = None) -> Any:
    tg_id = int(group["tg_id"])
    kind = (group.get("kind") or "supergroup").lower()
    if kind == "channel" and not group.get("is_megagroup"):
        return types.InputPeerChannel(channel_id=tg_id, access_hash=int(access_hash or group.get("access_hash") or 0))
    return types.InputPeerChannel(channel_id=tg_id, access_hash=int(access_hash or group.get("access_hash") or 0))


# ── данные о чате ──────────────────────────────────────────────────────────
async def fetch_history_meta(client: TelegramClient, peer: Any, limiter: RateLimiter) -> tuple[int | None, int | None]:
    """Возвращает ``(всего сообщений, дата последнего)``."""
    try:
        hist = await limiter.call(
            lambda: client(
                functions.messages.GetHistoryRequest(
                    peer=peer, offset_id=0, offset_date=None, add_offset=0,
                    limit=1, max_id=0, min_id=0, hash=0,
                )
            )
        )
    except Exception:  # noqa: BLE001
        return None, None
    total = getattr(hist, "count", None)
    last = None
    messages = getattr(hist, "messages", None) or []
    if messages:
        date = getattr(messages[0], "date", None)
        if date is not None:
            last = int(date.timestamp())
    return (int(total) if total is not None else None), last


async def fetch_chat_full(client: TelegramClient, peer: Any, limiter: RateLimiter) -> dict[str, Any]:
    try:
        full = await limiter.call(lambda: client(functions.channels.GetFullChannelRequest(channel=peer)))
    except Exception:  # noqa: BLE001
        return {}
    chat = getattr(full, "full_chat", None)
    if chat is None:
        return {}
    return {
        "members_count": getattr(chat, "participants_count", None),
        "about": getattr(chat, "about", None),
    }


async def find_participant(
    client: TelegramClient,
    group: dict[str, Any],
    target: dict[str, Any],
    limiter: RateLimiter,
    *,
    session_access_hash: Any = None,
    deep_pages: int = 0,
) -> tuple[str | None, int | None]:
    """Проверяет участие цели в чате.

    Возвращает ``(статус, access_hash цели)`` или ``(None, None)``, если цели
    в чате нет. ``status`` — ``member`` / ``admin`` / ``creator`` / ``left``.
    """
    peer = channel_peer(group, session_access_hash)
    target_peer = target_input_peer(target)
    try:
        result = await limiter.call(
            lambda: client(functions.channels.GetParticipantRequest(channel=peer, participant=target_peer))
        )
        participant = getattr(result, "participant", None)
        users = getattr(result, "users", None) or []
        hash_value = None
        for user in users:
            if isinstance(user, User) and user.id == int(target["tg_user_id"]):
                hash_value = getattr(user, "access_hash", None)
                break
        return participant_status(participant), hash_value
    except UserNotParticipantError:
        return None, None
    except ChannelPrivateError:
        raise
    except RPCError as exc:
        name = type(exc).__name__
        if name in {"ChannelInvalidError", "UserInvalidError", "UserIdInvalidError"}:
            # access_hash нулевой или чужой — пробуем найти по участникам
            pass
        elif name == "ChatAdminRequiredError":
            pass
        else:
            log.debug("getParticipant %s: %s", group.get("tg_id"), exc)

    # запасной путь: поиск по участникам (по username) или перебор страниц
    username = (target.get("username") or "").strip()
    if username:
        try:
            part = await limiter.call(
                lambda: client(
                    functions.channels.GetParticipantsRequest(
                        channel=peer,
                        filter=types.ChannelParticipantsSearch(q=username),
                        offset=0,
                        limit=200,
                        hash=0,
                    )
                )
            )
            for user in getattr(part, "users", None) or []:
                if isinstance(user, User) and user.id == int(target["tg_user_id"]):
                    return "member", getattr(user, "access_hash", None)
        except RPCError:
            pass

    for page in range(max(0, deep_pages)):
        try:
            part = await limiter.call(
                lambda page=page: client(
                    functions.channels.GetParticipantsRequest(
                        channel=peer,
                        filter=types.ChannelParticipantsRecent(),
                        offset=page * 200,
                        limit=200,
                        hash=0,
                    )
                )
            )
        except RPCError:
            break
        users = [u for u in (getattr(part, "users", None) or []) if isinstance(u, User)]
        for user in users:
            if user.id == int(target["tg_user_id"]):
                return "member", getattr(user, "access_hash", None)
        if len(users) < 200:
            break
    return None, None


async def join_public_group(client: TelegramClient, group: dict[str, Any], limiter: RateLimiter) -> bool:
    """Вступает в публичный чат по username. Возвращает True при успехе."""
    username = group.get("username")
    if not username:
        return False
    try:
        entity = await limiter.call(lambda: client.get_entity(username))
        await limiter.call(
            lambda: client(functions.channels.JoinChannelRequest(channel=entity))
        )
        return True
    except RPCError as exc:
        log.debug("join %s: %s", username, exc)
        return False


async def leave_group(client: TelegramClient, group: dict[str, Any], limiter: RateLimiter) -> bool:
    try:
        peer = channel_peer(group)
        await limiter.call(lambda: client(functions.channels.LeaveChannelRequest(channel=peer)))
        return True
    except RPCError:
        return False


def session_files(directory: Path) -> list[str]:
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.session") if p.is_file())
