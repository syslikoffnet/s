"""Доступ и антифлуд."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from ..config import Settings
from ..db import Store

log = logging.getLogger("healdragg.bot.access")

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class AccessMiddleware(BaseMiddleware):
    """Пускает владельца и тех, кто в ``BOT_ALLOWED_USERS``.

    Если список пуст и владелец ещё не привязан — первый ``/start`` забирает
    бота (владелец сохраняется в БД). Это защищает от чужих запросов без
    необходимости сразу лезть в .env.
    """

    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store
        self._owner_cache: int | None = None
        self._owner_loaded = False

    def _owner(self) -> int | None:
        if not self._owner_loaded:
            raw = self.store.get_meta("owner_user_id")
            self._owner_cache = int(raw) if raw and raw.lstrip("-").isdigit() else None
            self._owner_loaded = True
        return self._owner_cache

    def _claim(self, user_id: int) -> None:
        self.store.set_meta("owner_user_id", str(user_id))
        self._owner_cache = user_id
        self._owner_loaded = True
        self.store.log_event("info", f"владелец бота: {user_id}", "bot")

    def allowed(self, user_id: int) -> bool:
        if self.settings.bot_allow_all:
            return True
        if user_id in self.settings.bot_allowed_users:
            return True
        owner = self._owner()
        return owner is not None and user_id == owner

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)

        if self.allowed(user.id):
            return await handler(event, data)

        no_owner = self._owner() is None and not self.settings.bot_allowed_users
        is_start = isinstance(event, Message) and (event.text or "").startswith("/start")
        if no_owner and is_start:
            self._claim(user.id)
            log.info("бот привязан к %s", user.id)
            try:
                await event.answer(
                    "🔐 <b>Бот привязан к этому аккаунту.</b>\n\n"
                    f"Твой id: <code>{user.id}</code>\n"
                    "Он записан как владелец. Остальные пользователи получат отказ.\n\n"
                    "Изменить список: <code>BOT_ALLOWED_USERS</code> в .env."
                )
            except Exception:  # noqa: BLE001
                pass
            return await handler(event, data)

        log.info("отказ в доступе: %s (@%s)", user.id, user.username)
        if isinstance(event, CallbackQuery):
            await event.answer("Нет доступа", show_alert=True)
            return None
        if isinstance(event, Message):
            try:
                await event.answer(
                    "⛔ <b>Доступ закрыт</b>\n\n"
                    f"Твой id: <code>{user.id}</code>\n"
                    "Попроси владельца добавить его в <code>BOT_ALLOWED_USERS</code>."
                )
            except Exception:  # noqa: BLE001
                pass
        return None


class ThrottleMiddleware(BaseMiddleware):
    """Не больше одного действия в N секунд на пользователя."""

    def __init__(self, rate: float = 0.8) -> None:
        self.rate = rate
        self._last: dict[int, float] = {}

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        now = time.monotonic()
        last = self._last.get(user.id, 0.0)
        if now - last < self.rate:
            if isinstance(event, CallbackQuery):
                await event.answer("Секунду…")
                return None
            if isinstance(event, Message):
                return None
        self._last[user.id] = now
        if len(self._last) > 1000:
            cutoff = now - 300
            self._last = {k: v for k, v in self._last.items() if v > cutoff}
        return await handler(event, data)
