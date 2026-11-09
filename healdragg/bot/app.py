"""Сборка и запуск бота."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent

from ..config import Settings
from ..db import Store
from ..tg import SessionPool
from .deps import Deps
from .handlers import router
from .middlewares import AccessMiddleware, ThrottleMiddleware

log = logging.getLogger("healdragg.bot")

COMMANDS = [
    BotCommand(command="start", description="меню и поиск цели"),
    BotCommand(command="target", description="показать чаты по id / @username"),
    BotCommand(command="targets", description="список целей"),
    BotCommand(command="scan", description="проверить цель ещё раз"),
    BotCommand(command="stats", description="состояние базы"),
    BotCommand(command="find", description="поиск по каталогу групп"),
    BotCommand(command="id", description="мой Telegram ID"),
    BotCommand(command="stop", description="остановить сканирование"),
    BotCommand(command="help", description="как это работает"),
]

DESCRIPTION = (
    "🩺 Healdragg — карта публичных чатов Telegram.\n"
    "Пришли id или @username — покажу список чатов с датами, размером и статусом."
)
SHORT_DESCRIPTION = "Публичные чаты аккаунта: список, даты, админ/приватный/вышел."


def build_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.require_bot(),
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )


async def run_bot(settings: Settings, *, store: Store | None = None) -> None:
    settings.ensure_dirs()
    own_store = store is None
    store = store or Store(settings.db_path)
    pool = SessionPool(settings, store)
    deps = Deps(settings=settings, store=store, pool=pool)
    store.cancel_stale_scans()

    bot = build_bot(settings)
    deps.bot = bot
    dp = Dispatcher(storage=MemoryStorage())

    access = AccessMiddleware(settings, store)
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.message.middleware(ThrottleMiddleware(0.7))
    dp.callback_query.middleware(ThrottleMiddleware(0.35))

    dp.include_router(router)

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:  # noqa: ARG001
        log.exception("update failed: %s", event.exception)
        try:
            if event.update.message:
                await event.update.message.answer("💥 Что-то сломалось. Подробности — в логах.")
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        await bot.set_my_commands(COMMANDS)
        await bot.set_my_description(DESCRIPTION)
        await bot.set_my_short_description(SHORT_DESCRIPTION)
    except Exception as exc:  # noqa: BLE001
        log.debug("не удалось обновить профиль бота: %s", exc)

    stats = store.stats()
    log.info(
        "бот @healdragg_bot запущен · сессий %s · групп %s · целей %s",
        stats.get("sessions"),
        stats.get("groups"),
        stats.get("targets"),
    )
    if not settings.bot_allowed_users and not settings.bot_allow_all:
        log.warning("BOT_ALLOWED_USERS пуст — владельцем станет первый, кто напишет /start")

    try:
        await dp.start_polling(bot, deps=deps, allowed_updates=["message", "callback_query"])
    finally:
        await pool.close()
        try:
            await bot.session.close()
        except Exception:  # noqa: BLE001
            pass
        if own_store:
            store.close()
        log.info("бот остановлен")


def main() -> None:
    """Точка входа ``python -m healdragg.bot``."""
    from ..config import load_settings
    from ..log import setup_logging

    settings = load_settings()
    setup_logging(settings.log_level)
    try:
        asyncio.run(run_bot(settings))
    except KeyboardInterrupt:
        pass


__all__ = ["run_bot", "build_bot", "main"]
