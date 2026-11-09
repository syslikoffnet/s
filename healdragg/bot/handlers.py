"""Обработчики бота."""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..alerts import render_digest
from ..scan import bootstrap, resolve_and_store_target, scan_target
from ..textfmt import (
    EMPTY_DB,
    HELP,
    fit_telegram,
    render_account_chats,
    render_catalog,
    render_feed,
    render_network,
    render_profile,
    render_scan_progress,
    render_scan_report,
    render_stats,
    render_welcome,
)
from ..util import Page
from .deps import Deps
from .keyboards import (
    alerts_menu,
    cancel_job,
    catalog_pager,
    home,
    links_page,
    pager,
    scan_done,
    target_menu,
    targets_list,
)

log = logging.getLogger("healdragg.bot")
router = Router(name="main")

USERNAME_RE = re.compile(r"^@?[A-Za-z][A-Za-z0-9_]{3,31}$")
ID_RE = re.compile(r"^\d{5,15}$")
LINK_RE = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/", re.I)

PAGE_SIZE = 15


# ── утилиты ────────────────────────────────────────────────────────────────
def esc(text: str | None) -> str:
    return html.escape(text or "", quote=False)


async def safe_edit(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup=None,
) -> bool:
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=fit_telegram(text),
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return False
        log.warning("edit: %s", exc)
        return False


def looks_like_target(raw: str) -> bool:
    text = (raw or "").strip()
    if LINK_RE.match(text):
        return True
    text = text.lstrip("@")
    return bool(ID_RE.match(text) or USERNAME_RE.match(text))


def target_stats(deps: Deps, target: dict[str, Any]) -> dict[str, Any]:
    return deps.store.target_stats(int(target["id"]))


def render_target_block(target: dict[str, Any], stats: dict[str, Any], deps: Deps) -> str:
    text = render_profile(target, stats, tz=deps.settings.tz)
    if stats.get("total", 0) == 0:
        if not deps.store.count_groups():
            text += (
                "\n\n<i>База пуста — сначала собери публичные чаты "
                "(кнопка 🚀 в меню или <code>healdragg bootstrap</code>).</i>"
            )
        else:
            text += "\n\n<i>В каталоге нет чатов этой цели. Нажми «🔄 Обновить» — проверю по базе.</i>"
    return text


# ── вход ───────────────────────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message, deps: Deps, command: CommandObject | None = None) -> None:
    payload = (command.args if command else "") or ""
    stats = deps.store.stats()
    if payload and looks_like_target(payload):
        await _handle_target_input(message, payload, deps)
        return
    await message.answer(
        render_welcome(
            sessions=stats.get("sessions", 0),
            groups=stats.get("groups", 0),
            targets=stats.get("targets", 0),
        ),
        reply_markup=home(
            has_sessions=deps.has_sessions,
            has_groups=bool(stats.get("groups")),
            target_id=_last_target_id(deps),
        ),
    )


def _last_target_id(deps: Deps) -> int | None:
    targets = deps.store.list_targets(limit=1)
    return int(targets[0]["tg_user_id"]) if targets else None


@router.message(Command("help"))
async def cmd_help(message: Message, deps: Deps) -> None:
    await message.answer(HELP, reply_markup=home(has_sessions=deps.has_sessions, has_groups=True))


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    user = message.from_user
    await message.answer(
        "🪪 <b>Твои идентификаторы</b>\n"
        f"├ user id: <code>{user.id if user else '?'}</code>\n"
        f"├ чат: <code>{message.chat.id}</code>\n"
        "└ username: " + (f"@{user.username}" if user and user.username else "—")
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, deps: Deps) -> None:
    await message.answer(render_stats(deps.store.stats(), tz=deps.settings.tz), reply_markup=home(has_sessions=deps.has_sessions, has_groups=True))


@router.message(Command("targets"))
async def cmd_targets(message: Message, deps: Deps) -> None:
    await _send_targets(message, deps)


@router.message(Command("menu"))
async def cmd_menu(message: Message, deps: Deps) -> None:
    targets = deps.store.list_targets(limit=1)
    if not targets:
        await cmd_start(message, deps)
        return
    target = targets[0]
    stats = target_stats(deps, target)
    await message.answer(
        render_target_block(target, stats, deps),
        reply_markup=target_menu(int(target["tg_user_id"]), has_data=bool(stats.get("total"))),
    )


@router.message(Command("find"))
async def cmd_find(message: Message, deps: Deps, command: CommandObject | None = None) -> None:
    query = (command.args if command else "") or ""
    if not query:
        await message.answer("Напиши так: <code>/find музыка</code> — поищу по названию и @username.")
        return
    rows = deps.store.search_groups(query, limit=15)
    if not rows:
        await message.answer(f"По «{esc(query)}» в базе ничего нет. Попробуй другое слово или собери базу.")
        return
    page = Page.build(len(rows), 1, len(rows))
    await message.answer(
        render_catalog(rows, page=page, tz=deps.settings.tz, escape_html=True, numbered=False),
        reply_markup=home(has_sessions=deps.has_sessions, has_groups=True),
    )


@router.message(Command("stop"))
async def cmd_stop(message: Message, deps: Deps) -> None:
    cancelled = 0
    for key in list(deps.jobs):
        if key.startswith(("scan:", "boot")):
            if await deps.cancel(key):
                cancelled += 1
    await message.answer(f"⛔ Остановлено задач: {cancelled}" if cancelled else "Нечего останавливать.")


@router.message(Command("scan"))
async def cmd_scan(message: Message, deps: Deps, command: CommandObject | None = None) -> None:
    arg = (command.args if command else "") or ""
    target = deps.store.find_target(arg) if arg else None
    if target is None:
        targets = deps.store.list_targets(limit=1)
        target = targets[0] if targets else None
    if not target:
        await message.answer("Сначала пришли id или @username цели.")
        return
    if not deps.has_sessions:
        await message.answer("⚠️ Нет сессий: <code>healdragg session add main</code>")
        return
    key = f"scan:{target['tg_user_id']}"
    if deps.running(key):
        await message.answer("Уже сканирую эту цель.")
        return
    await _start_target_scan(message.chat.id, target, deps)


@router.message(Command("target"))
async def cmd_target(message: Message, deps: Deps, command: CommandObject | None = None) -> None:
    arg = (command.args if command else "") or ""
    if not arg:
        await message.answer("Напиши так: <code>/target 7126917488</code> или <code>/target @username</code>")
        return
    await _handle_target_input(message, arg, deps)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message, deps: Deps) -> None:
    raw = (message.text or "").strip()
    if not looks_like_target(raw):
        await message.answer(
            "🤔 Не похоже на аккаунт.\n\n"
            "Пришли <b>цифровой id</b> (например <code>7126917488</code>), "
            "<b>@username</b> или ссылку <code>t.me/username</code>.\n\n"
            "Либо жми <b>👁 Группы</b> в меню, если цель уже добавлена.",
            reply_markup=home(
                has_sessions=deps.has_sessions,
                has_groups=bool(deps.store.count_groups()),
                target_id=_last_target_id(deps),
            ),
        )
        return
    await _handle_target_input(message, raw, deps)


# ── цель ───────────────────────────────────────────────────────────────────
async def _send_targets(message: Message, deps: Deps) -> None:
    targets = deps.store.list_targets(limit=12)
    if not targets:
        await message.answer(
            "🎯 <b>Цели</b>\n\nПока пусто. Пришли id или @username — добавлю.",
        )
        return
    lines = [f"🎯 <b>Цели</b> · {len(targets)}"]
    for target in targets:
        stats = deps.store.target_stats(int(target["id"]))
        name = " ".join(
            p for p in (target.get("first_name"), target.get("last_name")) if p
        ) or (f"@{target['username']}" if target.get("username") else "без имени")
        lines.append(
            f"├ <code>{target['tg_user_id']}</code> · {esc(name)} · "
            f"<b>{stats.get('total', 0)}</b> чатов"
        )
    await message.answer(
        "\n".join(lines),
        reply_markup=targets_list(targets),
    )


@router.callback_query(F.data == "targets")
async def cb_targets(callback: CallbackQuery, deps: Deps) -> None:
    targets = deps.store.list_targets(limit=12)
    if not targets:
        await safe_edit(
            callback.bot,
            callback.message.chat.id,
            callback.message.message_id,
            "🎯 <b>Цели</b>\n\nПока пусто. Пришли id или @username — добавлю.",
            home(has_sessions=deps.has_sessions, has_groups=bool(deps.store.count_groups())),
        )
        await callback.answer()
        return
    lines = [f"🎯 <b>Цели</b> · {len(targets)}"]
    for target in targets:
        stats = deps.store.target_stats(int(target["id"]))
        name = " ".join(
            p for p in (target.get("first_name"), target.get("last_name")) if p
        ) or (f"@{target['username']}" if target.get("username") else "без имени")
        lines.append(
            f"├ <code>{target['tg_user_id']}</code> · {esc(name)} · "
            f"<b>{stats.get('total', 0)}</b> чатов"
        )
    await safe_edit(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        "\n".join(lines),
        targets_list(targets),
    )
    await callback.answer()


async def _handle_target_input(message: Message, raw: str, deps: Deps) -> None:
    status = await message.answer("🔎 Ищу аккаунт…")

    if not deps.has_sessions:
        if raw.strip().lstrip("@").isdigit() and not raw.strip().startswith(("@", "http")):
            target = deps.store.upsert_target(int(raw.strip().lstrip("@")))
            await safe_edit(
                message.bot,
                message.chat.id,
                status.message_id,
                "⚠️ <b>Нет ни одной сессии</b>\n\n"
                "Цель сохранена по id, но проверить чаты нечем.\n\n"
                "Добавь свой аккаунт в терминале:\n"
                "<code>healdragg session add main</code>\n"
                "потом собери базу:\n"
                "<code>healdragg bootstrap</code>\n\n" + render_target_block(target, target_stats(deps, target), deps),
                reply_markup=target_menu(int(target["tg_user_id"]), has_data=False),
            )
            return
        await safe_edit(
            message.bot,
            message.chat.id,
            status.message_id,
            "⚠️ <b>Нет ни одной сессии</b>\n\n"
            "Чтобы искать по @username, нужен свой аккаунт:\n"
            "<code>healdragg session add main</code>",
        )
        return

    try:
        target = await resolve_and_store_target(deps.pool, deps.store, raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("resolve %s: %s", raw, exc)
        if raw.strip().lstrip("@").isdigit():
            target = deps.store.upsert_target(int(raw.strip().lstrip("@")))
            await safe_edit(
                message.bot,
                message.chat.id,
                status.message_id,
                "⚠️ Не удалось получить профиль (нет доступа к аккаунту), "
                "но id сохранён.\n\n" + render_target_block(target, target_stats(deps, target), deps),
                reply_markup=target_menu(int(target["tg_user_id"]), has_data=False),
            )
            return
        await safe_edit(
            message.bot,
            message.chat.id,
            status.message_id,
            f"❌ Не нашёл аккаунт «{esc(raw)}».\n\n"
            "Проверь username или пришли цифровой id. "
            "Если аккаунт скрыт — id всё равно подойдёт.",
        )
        return

    stats = target_stats(deps, target)
    await safe_edit(
        message.bot,
        message.chat.id,
        status.message_id,
        render_target_block(target, stats, deps),
        reply_markup=target_menu(int(target["tg_user_id"]), has_data=bool(stats.get("total"))),
    )
    deps.store.log_event("info", f"цель добавлена: {target['tg_user_id']}", "bot")

    # чтобы «👁 Группы» не встречало пустотой — сразу проверяем по каталогу
    key = f"scan:{target['tg_user_id']}"
    if (
        deps.settings.auto_scan
        and not stats.get("total")
        and deps.store.count_groups() > 0
        and not deps.running(key)
    ):
        await _start_target_scan(message.chat.id, target, deps)


# ── кнопки: меню ───────────────────────────────────────────────────────────
def _alerts_view(deps: Deps, limit: int = 10) -> tuple[str, Any]:
    alerts = deps.store.list_alerts(limit=limit)
    text = render_digest(alerts, tz=deps.settings.tz)
    return text, alerts_menu(has_unread=deps.store.count_alerts() > 0)


@router.message(Command("alerts"))
async def cmd_alerts(message: Message, deps: Deps) -> None:
    text, keyboard = _alerts_view(deps)
    await message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)


@router.callback_query(F.data == "alerts")
async def cb_alerts(callback: CallbackQuery, deps: Deps) -> None:
    text, keyboard = _alerts_view(deps)
    await safe_edit(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        text,
        keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "alerts_read")
async def cb_alerts_read(callback: CallbackQuery, deps: Deps) -> None:
    changed = deps.store.mark_alerts_read()
    text, keyboard = _alerts_view(deps)
    await safe_edit(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        text,
        keyboard,
    )
    await callback.answer(f"прочитано: {changed}")


@router.callback_query(F.data == "home")
async def cb_home(callback: CallbackQuery, deps: Deps) -> None:
    stats = deps.store.stats()
    await safe_edit(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        render_welcome(
            sessions=stats.get("sessions", 0),
            groups=stats.get("groups", 0),
            targets=stats.get("targets", 0),
        ),
        home(
            has_sessions=deps.has_sessions,
            has_groups=bool(stats.get("groups")),
            target_id=_last_target_id(deps),
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "stats")
async def cb_stats(callback: CallbackQuery, deps: Deps) -> None:
    await safe_edit(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        render_stats(deps.store.stats(), tz=deps.settings.tz),
        home(has_sessions=deps.has_sessions, has_groups=True, target_id=_last_target_id(deps)),
    )
    await callback.answer()


@router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery, deps: Deps) -> None:
    await safe_edit(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        HELP,
        home(has_sessions=deps.has_sessions, has_groups=bool(deps.store.count_groups())),
    )
    await callback.answer()


@router.callback_query(F.data == "find")
async def cb_find(callback: CallbackQuery, deps: Deps) -> None:
    await callback.answer()
    await callback.message.answer(
        "🔍 <b>Как искать</b>\n\n"
        "Пришли одно из:\n"
        "├ <code>7126917488</code> — цифровой id\n"
        "├ <code>@username</code>\n"
        "└ <code>t.me/username</code>\n\n"
        "Я сохраню цель и покажу кнопку <b>👁 Группы</b>.\n"
        "Поиск по каталогу: <code>/find музыка</code>"
    )


@router.callback_query(F.data == "nosessions")
async def cb_nosessions(callback: CallbackQuery) -> None:
    await callback.answer("Нет сессий", show_alert=True)
    await callback.message.answer(
        "⚠️ Сначала добавь сессию в терминале:\n"
        "<code>healdragg session add main</code>\n\n"
        "Потом вернись и нажми 🚀."
    )


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# ── кнопки: группы цели ────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("g:"))
async def cb_groups(callback: CallbackQuery, deps: Deps) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await callback.answer("Кнопка устарела", show_alert=True)
        return
    tg_id = int(parts[1])
    page_num = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    target = deps.store.get_target(tg_id)
    if not target:
        await callback.answer("Цель не найдена — пришли id заново", show_alert=True)
        return

    total = deps.store.count_target_groups(int(target["id"]))
    page = Page.build(total, page_num, deps.settings.page_size)

    if total == 0:
        text = (
            "👁 <b>Группы</b>\n\n"
            "Пока пусто: цель ещё не проверена по каталогу.\n"
            + ("Каталог тоже пуст — сначала нажми 🚀 <b>Собрать базу</b>."
               if not deps.store.count_groups()
               else "Нажми <b>🔄 Обновить</b> — проверю участие по базе.")
        )
        await safe_edit(
            callback.bot,
            callback.message.chat.id,
            callback.message.message_id,
            text,
            target_menu(tg_id, has_data=False),
        )
        await callback.answer()
        return

    rows = deps.store.list_target_groups(int(target["id"]), page=page.page, size=page.size)
    text = render_account_chats(
        rows,
        target_id=tg_id,
        page=page,
        tz=deps.settings.tz,
        title_limit=deps.settings.title_limit,
        escape_html=True,
    )
    await safe_edit(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        text,
        pager(
            tg_id,
            page,
            extra=[[InlineKeyboardButton(text="🔗 Ссылки", callback_data=f"lnk:{tg_id}:{page.page}")]],
        ),
    )
    await callback.answer(f"страница {page.page} из {page.pages}")


def _links_text(total: int, page: Page) -> str:
    return (
        "🔗 <b>Ссылки на чаты</b>\n"
        f"cтρаница {page.page} из {page.pages} · всего <b>{total}</b>\n\n"
        "Нажми на чат — откроется ссылка.\n"
        "<i>Публичные: t.me/username · приватные: t.me/c/… "
        "(работают у участников).</i>"
    )


def _links_payload(deps: Deps, target: dict[str, Any], page_num: int) -> tuple[str, InlineKeyboardMarkup]:
    total = deps.store.count_target_groups(int(target["id"]))
    page = Page.build(total, page_num, deps.settings.page_size)
    rows = deps.store.list_target_groups(int(target["id"]), page=page.page, size=page.size)
    return _links_text(total, page), links_page(rows, int(target["tg_user_id"]), page)


@router.callback_query(F.data.startswith("lnk:"))
async def cb_links(callback: CallbackQuery, deps: Deps) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await callback.answer("Кнопка устарела", show_alert=True)
        return
    tg_id = int(parts[1])
    page_num = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    target = deps.store.get_target(tg_id)
    if not target:
        await callback.answer("Цель не найдена — пришли id заново", show_alert=True)
        return
    text, keyboard = _links_payload(deps, target, page_num)
    await safe_edit(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        text,
        keyboard,
    )
    await callback.answer()


@router.message(Command("links"))
async def cmd_links(message: Message, deps: Deps, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    target = deps.store.find_target(raw) if raw else None
    if not target:
        targets = deps.store.list_targets(limit=1)
        target = targets[0] if targets else None
    if not target:
        await message.answer(
            "Пришли id или @username цели — и я покажу ссылки на её чаты.",
            reply_markup=home(has_sessions=deps.has_sessions, has_groups=bool(deps.store.count_groups())),
        )
        return
    text, keyboard = _links_payload(deps, target, 1)
    await message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)


def _target_from_command(deps: Deps, command: CommandObject | None) -> dict[str, Any] | None:
    raw = ((command.args if command else "") or "").strip()
    target = deps.store.find_target(raw) if raw else None
    if not target:
        targets = deps.store.list_targets(limit=1)
        target = targets[0] if targets else None
    return target


@router.message(Command("network", "graph"))
async def cmd_network(message: Message, deps: Deps, command: CommandObject) -> None:
    target = _target_from_command(deps, command)
    if not target:
        await message.answer("Пришли id цели — покажу, с кем она общается.")
        return
    target_pk = int(target["id"])
    edges = deps.store.target_edges(target_pk, limit=10)
    stats = deps.store.activity_stats(target_pk)
    await message.answer(
        render_network(edges, stats, tz=deps.settings.tz),
        disable_web_page_preview=True,
    )


@router.message(Command("feed"))
async def cmd_feed(message: Message, deps: Deps, command: CommandObject) -> None:
    target = _target_from_command(deps, command)
    if not target:
        await message.answer("Пришли id цели — покажу, что она пишет.")
        return
    rows = deps.store.activity_feed(int(target["id"]), limit=8)
    await message.answer(
        render_feed(rows, tz=deps.settings.tz),
        disable_web_page_preview=True,
    )


@router.message(Command("activity"))
async def cmd_activity(message: Message, deps: Deps, command: CommandObject) -> None:
    target = _target_from_command(deps, command)
    if not target:
        await message.answer("Пришли id цели — соберу её сообщения и связи.")
        return
    tg_id = int(target["tg_user_id"])
    if deps.store.count_target_groups(int(target["id"])) == 0:
        await message.answer("Сначала нужен список чатов: /target " + str(tg_id))
        return
    await message.answer(
        "📡 Собираю последние сообщения и связи. Это может занять пару минут — "
        "пришлю итог, когда закончу."
    )

    async def run() -> None:
        from ..activity import collect_activity

        try:
            await collect_activity(
                deps.pool,
                deps.store,
                deps.settings,
                target,
                days=deps.settings.activity_days,
                per_chat=deps.settings.activity_per_chat,
                max_chats=deps.settings.activity_max_chats,
            )
            stats = deps.store.activity_stats(int(target["id"]))
            edges = deps.store.target_edges(int(target["id"]), limit=10)
            await message.answer(
                render_network(edges, stats, tz=deps.settings.tz),
                disable_web_page_preview=True,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("activity failed")
            await message.answer(f"⚠️ Не удалось собрать активность: {exc}")

    asyncio.create_task(run())


@router.callback_query(F.data.startswith("p:"))
async def cb_profile(callback: CallbackQuery, deps: Deps) -> None:
    tg_id = int((callback.data or "p:0").split(":")[1] or 0)
    target = deps.store.get_target(tg_id)
    if not target:
        await callback.answer("Цель не найдена", show_alert=True)
        return
    stats = target_stats(deps, target)
    await safe_edit(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        render_target_block(target, stats, deps),
        target_menu(tg_id, has_data=bool(stats.get("total"))),
    )
    await callback.answer()


# ── кнопки: база ───────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("catalog:"))
async def cb_catalog(callback: CallbackQuery, deps: Deps) -> None:
    parts = (callback.data or "").split(":")
    page_num = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    total = deps.store.count_groups()
    if total == 0:
        await safe_edit(
            callback.bot,
            callback.message.chat.id,
            callback.message.message_id,
            EMPTY_DB,
            home(has_sessions=deps.has_sessions, has_groups=False),
        )
        await callback.answer()
        return
    page = Page.build(total, page_num, deps.settings.page_size)
    rows = deps.store.list_groups(page=page.page, size=page.size, order="last")
    await safe_edit(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        render_catalog(rows, page=page, tz=deps.settings.tz, escape_html=True),
        catalog_pager(page),
    )
    await callback.answer(f"{page.page}/{page.pages}")


# ── кнопки: сканы ──────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("r:"))
async def cb_refresh(callback: CallbackQuery, deps: Deps) -> None:
    tg_id = int((callback.data or "r:0").split(":")[1] or 0)
    target = deps.store.get_target(tg_id)
    if not target:
        await callback.answer("Цель не найдена", show_alert=True)
        return
    key = f"scan:{tg_id}"
    if deps.running(key):
        await callback.answer("Уже сканирую…", show_alert=True)
        return
    if not deps.has_sessions:
        await callback.answer("Нет сессий", show_alert=True)
        await callback.message.answer("⚠️ Сначала: <code>healdragg session add main</code>")
        return
    await callback.answer("Запускаю")
    await _start_target_scan(callback.message.chat.id, target, deps)


@router.callback_query(F.data == "boot")
async def cb_boot(callback: CallbackQuery, deps: Deps) -> None:
    if deps.running("boot"):
        await callback.answer("Сбор уже идёт…", show_alert=True)
        return
    if not deps.has_sessions:
        await callback.answer("Нет сессий", show_alert=True)
        await callback.message.answer("⚠️ Сначала: <code>healdragg session add main</code>")
        return
    await callback.answer("Поехали")
    await _start_bootstrap(callback.message.chat.id, deps)


@router.callback_query(F.data.startswith("job:cancel:"))
async def cb_cancel(callback: CallbackQuery, deps: Deps) -> None:
    parts = (callback.data or "").split(":")
    # job:cancel:scan:7126917488  |  job:cancel:boot
    key = ":".join(parts[2:]) if len(parts) > 2 else ""
    ok = await deps.cancel(key)
    await callback.answer("Остановлено" if ok else "Уже не идёт", show_alert=not ok)


# ── фоновые задачи с прогрессом ────────────────────────────────────────────
async def _start_target_scan(chat_id: int, target: dict[str, Any], deps: Deps) -> None:
    key = f"scan:{target['tg_user_id']}"
    bot = _bot(deps)
    msg = await bot.send_message(
        chat_id,
        "⏳ <b>Стартую проверку</b>\nСейчас подключусь к Telegram…",
        reply_markup=cancel_job("scan", str(target["tg_user_id"])),
    )

    async def runner() -> None:
        started = time.monotonic()
        last = {"ts": 0.0}

        async def on_progress(state: dict[str, Any]) -> None:
            now = time.monotonic()
            if now - last["ts"] < 3.0:
                return
            last["ts"] = now
            fake = {
                "total": state.get("total", 0),
                "done": state.get("done", 0),
                "found": state.get("found", 0),
                "started_at": int(time.time() - (now - started)),
            }
            title = f"⏳ {state.get('phase', 'сканирую')}…"
            await safe_edit(
                bot,
                chat_id,
                msg.message_id,
                render_scan_progress(fake, title=title),
                cancel_job("scan", str(target["tg_user_id"])),
            )

        try:
            result = await scan_target(
                deps.pool,
                deps.store,
                deps.settings,
                target,
                reporter=on_progress,
            )
            stats = deps.store.target_stats(int(target["id"]))
            report = render_scan_report(
                kind=str(result.get("mode") or "probe"),
                stats={"checked": result.get("checked", 0), "found": stats.get("total", 0)},
                elapsed=result.get("elapsed"),
                errors=int(result.get("errors", 0)),
            )
            await safe_edit(
                bot,
                chat_id,
                msg.message_id,
                report + "\n\n" + render_profile(target, stats, tz=deps.settings.tz),
                scan_done(int(target["tg_user_id"])),
            )
        except asyncio.CancelledError:
            await safe_edit(bot, chat_id, msg.message_id, "⛔ <b>Отменено</b>", scan_done(int(target["tg_user_id"])))
        except Exception as exc:  # noqa: BLE001
            log.exception("scan failed")
            await safe_edit(
                bot,
                chat_id,
                msg.message_id,
                f"❌ <b>Ошибка скана</b>\n<code>{esc(str(exc))[:300]}</code>",
                scan_done(int(target["tg_user_id"])),
            )

    task = asyncio.create_task(runner(), name=key)
    deps.register(key, task, kind="scan", target_pk=int(target["id"]))


async def _start_bootstrap(chat_id: int, deps: Deps) -> None:
    bot = _bot(deps)
    msg = await bot.send_message(
        chat_id,
        "🚀 <b>Собираю базу</b>\nЧитаю диалоги и ищу публичные чаты…",
        reply_markup=cancel_job("boot"),
    )

    async def runner() -> None:
        started = time.monotonic()
        last = {"ts": 0.0}

        async def on_progress(state: dict[str, Any]) -> None:
            now = time.monotonic()
            if now - last["ts"] < 3.0:
                return
            last["ts"] = now
            fake = {
                "total": state.get("total", 0),
                "done": state.get("done", 0),
                "found": state.get("found", 0),
                "started_at": int(time.time() - (now - started)),
            }
            await safe_edit(
                bot,
                chat_id,
                msg.message_id,
                render_scan_progress(fake, title=f"🚀 {state.get('phase', 'собираю базу')}…"),
                cancel_job("boot"),
            )

        try:
            result = await bootstrap(
                deps.pool,
                deps.store,
                deps.settings,
                join=deps.settings.bootstrap_join,
                join_limit=deps.settings.join_limit,
                reporter=on_progress,
            )
            stats = deps.store.stats()
            report = render_scan_report(
                kind="bootstrap",
                stats={"checked": result.get("checked", 0), "found": result.get("groups", 0)},
                elapsed=result.get("elapsed"),
            )
            await safe_edit(
                bot,
                chat_id,
                msg.message_id,
                report + "\n\n" + render_stats(stats, tz=deps.settings.tz),
                home(has_sessions=deps.has_sessions, has_groups=True, target_id=_last_target_id(deps)),
            )
        except asyncio.CancelledError:
            await safe_edit(bot, chat_id, msg.message_id, "⛔ <b>Сбор остановлен</b>")
        except Exception as exc:  # noqa: BLE001
            log.exception("bootstrap failed")
            await safe_edit(
                bot,
                chat_id,
                msg.message_id,
                f"❌ <b>Ошибка сбора</b>\n<code>{esc(str(exc))[:300]}</code>",
                home(has_sessions=deps.has_sessions, has_groups=bool(deps.store.count_groups())),
            )

    key = "boot"
    task = asyncio.create_task(runner(), name=key)
    deps.register(key, task, kind="bootstrap")


# бот прокидывается в Deps при старте (см. app.py)
def _bot(deps: Deps):
    bot = getattr(deps, "bot", None)
    if bot is None:
        raise RuntimeError("бот не инициализирован")
    return bot


__all__ = ["router"]
