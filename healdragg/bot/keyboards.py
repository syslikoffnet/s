"""Клавиатуры бота. Callback-данные короткие — лимит Telegram 64 байта."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..util import Page

GROUPS_BUTTON = "👁 Группы"


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def home(
    *,
    has_sessions: bool,
    has_groups: bool,
    target_id: int | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if target_id:
        rows.append([_btn(GROUPS_BUTTON, f"g:{target_id}:1")])
        rows.append([_btn("🔄 Обновить", f"r:{target_id}"), _btn("📊 Профиль", f"p:{target_id}")])
        rows.append([_btn("🎯 Цели", "targets"), _btn("📚 База групп", "catalog:1")])
    else:
        rows.append([_btn("🔍 Как искать", "find"), _btn("📚 База групп", "catalog:1")])
    if not has_groups:
        rows.append([_btn("🚀 Собрать базу", "boot" if has_sessions else "nosessions")])
    rows.append([_btn("📈 Статистика", "stats"), _btn("❓ Помощь", "help")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def targets_list(targets: Sequence[dict], *, active_id: int | None = None) -> InlineKeyboardMarkup:
    """Список целей: по две кнопки в ряд."""
    buttons: list[InlineKeyboardButton] = []
    for target in targets[:12]:
        tg_id = int(target["tg_user_id"])
        name = (
            f"@{target['username']}" if target.get("username")
            else target.get("first_name") or str(tg_id)
        )
        label = f"{'· ' if tg_id == active_id else ''}{name}"
        buttons.append(_btn(label[:28], f"p:{tg_id}"))
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)] or [
        [_btn("Пока пусто", "noop")]
    ]
    rows.append([_btn("🏠 Меню", "home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def target_menu(target_id: int, *, has_data: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [[_btn(GROUPS_BUTTON, f"g:{target_id}:1")]]
    rows.append(
        [
            _btn("🔄 Обновить", f"r:{target_id}"),
            _btn("📊 Профиль", f"p:{target_id}"),
            _btn("🏠 Меню", "home"),
        ]
    )
    if not has_data:
        rows.insert(0, [_btn("🚀 Собрать базу", "boot")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pager(
    target_id: int,
    page: Page,
    *,
    prefix: str = "g",
    extra: Sequence[Sequence[InlineKeyboardButton]] = (),
) -> InlineKeyboardMarkup:
    """Навигация: «1 ⬅ · 2/8 · ➡ 3» + номерные кнопки."""
    rows: list[list[InlineKeyboardButton]] = []

    nav: list[InlineKeyboardButton] = []
    if page.has_prev:
        nav.append(_btn(f"{page.page - 1} ⬅", f"{prefix}:{target_id}:{page.page - 1}"))
    nav.append(_btn(f"· {page.page}/{page.pages} ·", "noop"))
    if page.has_next:
        nav.append(_btn(f"➡ {page.page + 1}", f"{prefix}:{target_id}:{page.page + 1}"))
    if len(nav) > 1:
        rows.append(nav)

    numbers: list[InlineKeyboardButton] = []
    for num in page.window(radius=2):
        if num < 0:
            numbers.append(_btn("…", "noop"))
        elif num == page.page:
            numbers.append(_btn(f"·{num}·", "noop"))
        else:
            numbers.append(_btn(str(num), f"{prefix}:{target_id}:{num}"))
    if len(numbers) > 1:
        for start in range(0, len(numbers), 5):
            rows.append(numbers[start : start + 5])

    rows.extend(list(extra))
    rows.append(
        [
            _btn("🔄 Обновить", f"r:{target_id}"),
            _btn("📊 Профиль", f"p:{target_id}"),
            _btn("🏠 Меню", "home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_pager(page: Page, *, extra: Sequence[Sequence[InlineKeyboardButton]] = ()) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if page.has_prev:
        nav.append(_btn(f"{page.page - 1} ⬅", f"catalog:{page.page - 1}"))
    nav.append(_btn(f"· {page.page}/{page.pages} ·", "noop"))
    if page.has_next:
        nav.append(_btn(f"➡ {page.page + 1}", f"catalog:{page.page + 1}"))
    rows.append(nav)
    rows.extend(list(extra))
    rows.append([_btn("⬅️ Назад", "home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_job(kind: str, ident: str = "") -> InlineKeyboardMarkup:
    data = f"job:cancel:{kind}:{ident}".rstrip(":")
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("⛔ Отменить", data)]])


def scan_done(target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(GROUPS_BUTTON, f"g:{target_id}:1")],
            [_btn("🏠 Меню", "home")],
        ]
    )


def simple(*rows: Iterable[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(text, data) for text, data in row] for row in rows]
    )
