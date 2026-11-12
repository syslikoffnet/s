"""Текстовые представления Healdragg.

Здесь живёт «фирменный» вид списка чатов — тот самый, с глазиком, замочком,
крестиком и постраничной навигацией. Формат вынесен в отдельный модуль, чтобы
бот, терминал, веб и Android показывали одно и то же.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .util import (
    Page,
    fmt_day,
    fmt_dt,
    fmt_relative,
    human_duration,
    human_size,
    now_ts,
    truncate,
)

# ── константы формата ──────────────────────────────────────────────────────
# Кириллица с греческими омоглифами — как в оригинале: меньше шансов попасть
# под автоматические фильтры Telegram.
HEADER = "ㅤЧаты аккаунта {target}."
LEGEND = "👮🏻‍♂️-админ, 🔒-приватный, ✖️-вышел"
COLUMNS = "Посл. сοобщ - чат (всего сообщений)"
FOOTER = "Всего {total}, cтρаница {page} из {pages}"

HEADER_PLAIN = "ㅤЧаты аккаунта {target}."
LEGEND_PLAIN = "👮🏻‍♂️-админ, 🔒-приватный, ✖️-вышел"
COLUMNS_PLAIN = "Посл. сообщ - чат (всего сообщений)"
FOOTER_PLAIN = "Всего {total}, страница {page} из {pages}"

MARK_ADMIN = "👮🏻‍♂️"
MARK_PRIVATE = "🔒"
MARK_LEFT = "✖️"

TG_LIMIT = 4096
TG_CAPTION_LIMIT = 1024


# ── строки списка ──────────────────────────────────────────────────────────
@dataclass(slots=True)
class ChatRow:
    """Одна строка «Чаты аккаунта»."""

    title: str
    tg_id: int = 0
    username: str | None = None
    is_public: bool = True
    status: str = "member"
    last_message_at: int | None = None
    messages_total: int | None = None
    members_count: int | None = None
    source: str | None = None

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> ChatRow:
        return cls(
            title=row.get("title") or "Без названия",
            tg_id=int(row.get("tg_id") or 0),
            username=row.get("username"),
            is_public=bool(row.get("is_public", bool(row.get("username")))),
            status=row.get("status") or "member",
            last_message_at=row.get("last_message_at"),
            messages_total=row.get("messages_total"),
            members_count=row.get("members_count"),
            source=row.get("source"),
        )

    @property
    def markers(self) -> str:
        if self.status == "left":
            return MARK_LEFT
        out = ""
        if self.status in {"admin", "creator"}:
            out += MARK_ADMIN
        if not self.is_public:
            out += MARK_PRIVATE
        return out

    def line(
        self,
        *,
        tz: str = "Europe/Moscow",
        title_limit: int = 15,
        suffix: str = "..",
        escape_html: bool = False,
    ) -> str:
        title = truncate(self.title, title_limit, suffix=suffix)
        if escape_html:
            title = html.escape(title, quote=False)
        day = fmt_day(self.last_message_at, tz)
        total = "—" if self.messages_total is None else str(int(self.messages_total))
        return f" {self.markers}{day} {title} ({total})"


def chat_rows(rows: Iterable[dict[str, Any]]) -> list[ChatRow]:
    return [ChatRow.from_mapping(r) for r in rows]


def render_account_chats(
    rows: Sequence[dict[str, Any] | ChatRow],
    *,
    target_id: int | str,
    page: Page,
    tz: str = "Europe/Moscow",
    title_limit: int = 15,
    homoglyph: bool = True,
    blank_lines: bool = False,
    header_note: str | None = None,
    escape_html: bool = False,
) -> str:
    """Собирает сообщение «Чаты аккаунта N» ровно как в оригинале."""
    head = HEADER if homoglyph else HEADER_PLAIN
    legend = LEGEND if homoglyph else LEGEND_PLAIN
    columns = COLUMNS if homoglyph else COLUMNS_PLAIN
    footer = FOOTER if homoglyph else FOOTER_PLAIN

    crowd = [r if isinstance(r, ChatRow) else ChatRow.from_mapping(r) for r in rows]
    sep = "\n\n" if blank_lines else "\n"
    body = (
        sep.join(r.line(tz=tz, title_limit=title_limit, escape_html=escape_html) for r in crowd)
        if crowd
        else " Пока пусто…"
    )

    parts = [head.format(target=target_id)]
    if header_note:
        parts.append(header_note)
    parts.extend([legend, columns])
    text = "\n".join(parts) + "\n\n" + body + "\n\n" + footer.format(
        total=page.total, page=page.page, pages=page.pages
    )
    return text


_TAG_RE = re.compile(r"</?(?:b|i|code|u|s|pre|a)(?:\s[^>]*)?>", re.I)


def to_plain(text: str) -> str:
    """Убирает простые HTML-теги, чтобы показать текст в терминале."""
    import html as _html

    return _html.unescape(_TAG_RE.sub("", text or ""))


def fit_telegram(text: str, limit: int = TG_LIMIT) -> str:
    """Страховка от лимита сообщения: режем по строкам."""
    if len(text) <= limit:
        return text
    out: list[str] = []
    size = 0
    for line in text.splitlines():
        if size + len(line) + 1 > limit - 20:
            break
        out.append(line)
        size += len(line) + 1
    out.append("…")
    return "\n".join(out)


# ── профиль, статистика, каталог ───────────────────────────────────────────
def _name(target: dict[str, Any]) -> str:
    full = " ".join(p for p in (target.get("first_name"), target.get("last_name")) if p).strip()
    return full or (target.get("username") and f"@{target['username']}") or str(target.get("tg_user_id"))


def render_profile(target: dict[str, Any], stats: dict[str, Any], *, tz: str = "Europe/Moscow") -> str:
    username = f"@{target['username']}" if target.get("username") else "без username"
    lines = [
        f"👤 <b>Профиль</b> · <code>{target.get('tg_user_id')}</code>",
        f"├ {_name(target)} · {username}",
        f"├ 💬 Чатов в базе: <b>{stats.get('total', 0)}</b>",
        "├ 👮🏻‍♂️ админ: {admin} · 🔒 приватных: {private} · ✖️ вышел: {left_count}".format(
            admin=stats.get("admin", 0),
            private=stats.get("private", 0),
            left_count=stats.get("left_count", 0),
        ),
        f"└ 🕒 Обновлено: {fmt_dt(stats.get('updated_at') or target.get('last_scan_at'), tz)}",
    ]
    return "\n".join(lines)


def grouped_int(value: int | float | None) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}".replace(",", " ")


def render_network(
    edges: Sequence[dict[str, Any]],
    stats: dict[str, Any],
    *,
    tz: str = "Europe/Moscow",
    limit: int = 10,
) -> str:
    """Топ собеседников цели + счётчики — для бота и консоли."""
    lines = [
        "🕸 <b>Сеть связей</b>",
        "",
        f"💬 сообщений: <b>{stats.get('messages', 0)}</b> · "
        f"👥 собеседников: <b>{stats.get('peers', 0)}</b> · "
        f"↩ ответов: <b>{stats.get('replies', 0)}</b>",
        "",
    ]
    if not edges:
        lines.append("Пока пусто — собери активность: /activity или `healdragg activity <id>`.")
        return "\n".join(lines)
    lines.append("Топ собеседников:")
    for index, edge in enumerate(edges[:limit], start=1):
        name = " ".join(p for p in (edge.get("first_name"), edge.get("last_name")) if p).strip()
        if not name:
            name = f"@{edge['username']}" if edge.get("username") else f"ID {edge['tg_id']}"
        bits = [f"{index}. <b>{html.escape(name)}</b>"]
        if edge.get("weight"):
            bits.append(f"· {edge['weight']}")
        if edge.get("replies"):
            bits.append(f"↩{edge['replies']}")
        if edge.get("mentions"):
            bits.append(f"@ {edge['mentions']}")
        if edge.get("last_seen"):
            bits.append(f"· {fmt_relative(edge['last_seen'], tz)}")
        lines.append(" ".join(bits))
    lines.append("")
    lines.append("<i>Паутина: панель → 🕸 Сеть или `healdragg graph <id> --html graph.html`</i>")
    return "\n".join(lines)


def render_feed(
    rows: Sequence[dict[str, Any]],
    *,
    tz: str = "Europe/Moscow",
    limit: int = 8,
    title: str = "📜 Что пишет",
) -> str:
    """Последние сообщения цели — компактно, с ссылками на чаты."""
    if not rows:
        return (
            f"{title}\n\nПока пусто — собери активность: /activity "
            "или `healdragg activity <id>`."
        )
    lines = [f"{title}", ""]
    for row in rows[:limit]:
        when = fmt_relative(row.get("date"), tz)
        chat = row.get("title") or "чат"
        link = row.get("link")
        chat_html = (
            f'<a href="{html.escape(link)}">{html.escape(truncate(chat, 24))}</a>'
            if link
            else html.escape(truncate(chat, 24))
        )
        text = (row.get("text") or "").strip().replace("\n", " ")
        if text:
            text = html.escape(truncate(text, 180))
        else:
            text = "<i>без текста</i>"
        media = f" · {html.escape(row['media'])}" if row.get("media") else ""
        reply = " · ↩" if row.get("reply_to_peer") else ""
        lines.append(f"▪ {when} · {chat_html}{media}{reply}\n{text}")
        lines.append("")
    lines.append("<i>Панель → 📜 Лента</i>")
    return "\n".join(lines)


def render_stats(stats: dict[str, Any], *, tz: str = "Europe/Moscow", title: str = "📚 База Healdragg") -> str:
    lines = [
        f"<b>{title}</b>",
        f"├ 🗂 Групп: <b>{grouped_int(stats.get('groups'))}</b> · публичных {grouped_int(stats.get('public_groups'))}",
        f"├ 🔗 Покрытие: {grouped_int(stats.get('coverage_groups') or stats.get('groups'))} "
        f"чатов / {grouped_int(stats.get('covered_sessions') or stats.get('sessions'))} сессий",
        f"├ 👥 Целей: {grouped_int(stats.get('targets'))} · связей {grouped_int(stats.get('target_groups'))}",
        f"├ 💾 Размер базы: {human_size(stats.get('db_bytes') or 0)}",
        f"└ 🕒 Обновлено: {fmt_relative(stats.get('updated_at'), tz)}",
    ]
    return "\n".join(lines)


def render_catalog(
    groups: Sequence[dict[str, Any]],
    *,
    page: Page,
    tz: str = "Europe/Moscow",
    title_limit: int = 28,
    numbered: bool = True,
    escape_html: bool = False,
) -> str:
    lines = [f"🗂 <b>База групп</b> · {grouped_int(page.total)} записей", ""]
    if not groups:
        lines.append("Пока пусто. Запусти <code>healdragg bootstrap</code> или нажми «Собрать базу».")
        return "\n".join(lines)
    for idx, g in enumerate(groups, start=page.offset + 1):
        title = truncate(g.get("title") or "Без названия", title_limit)
        user = f"@{g['username']}" if g.get("username") else "private"
        if escape_html:
            title = html.escape(title, quote=False)
            user = html.escape(user, quote=False)
        num = f"{idx:>2}. " if numbered else "• "
        members = grouped_int(g.get("members_count")) if g.get("members_count") else "—"
        last = fmt_day(g.get("last_message_at"), tz)
        lines.append(f"{num}{title} · {user} · 👥{members} · {last}")
    lines += ["", f"cтρаница {page.page} из {page.pages}"]
    return "\n".join(lines)


def render_group_line(group: dict[str, Any], *, tz: str = "Europe/Moscow", title_limit: int = 40) -> str:
    title = truncate(group.get("title") or "Без названия", title_limit)
    user = f"@{group['username']}" if group.get("username") else "🔒 приватный"
    members = grouped_int(group.get("members_count")) if group.get("members_count") else "—"
    total = grouped_int(group.get("messages_total")) if group.get("messages_total") else "—"
    return (
        f"<b>{title}</b>\n"
        f"└ {user} · 👥 {members} · 💬 {total} · {fmt_day(group.get('last_message_at'), tz)}"
    )


def progress_bar(done: int, total: int, width: int = 12) -> str:
    if total <= 0:
        return "░" * width
    filled = min(width, max(0, round(width * done / total)))
    return "▓" * filled + "░" * (width - filled)


def render_scan_progress(scan: dict[str, Any], *, title: str = "⏳ Сканирую…") -> str:
    total = int(scan.get("total") or 0)
    done = int(scan.get("done") or 0)
    found = int(scan.get("found") or 0)
    pct = (done * 100 // total) if total else 0
    return (
        f"{title}\n"
        f"{progress_bar(done, total)} {done}/{total} · {pct}%\n"
        f"├ 🆕 найдено чатов: <b>{found}</b>\n"
        f"└ ⏱ прошло: {human_duration(now_ts() - int(scan.get('started_at') or now_ts()))}"
    )


# ── бот: приветствие и подсказки ───────────────────────────────────────────
WELCOME = """\
<b>🩺 Healdragg</b> — монитор публичных чатов Telegram.

Пришли <b>id</b> или <b>@username</b> — покажу:
• 👁 чаты со ссылками и датами
• 🕸 паутину связей: с кем и как часто общается
• 📜 ленту его сообщений
• 🔔 алерт, когда он заходит в новые чаты

<i>Только открытые группы. Всё лежит в твоей локальной базе.</i>"""

HELP = """\
<b>Как это работает</b>

1. <b>Добавь сессию</b> (свой аккаунт) в терминале:
   <code>healdragg session add main</code>
2. <b>Наполни базу</b> публичными чатами:
   <code>healdragg bootstrap</code> или кнопка 🚀
3. <b>Пришли id</b> @username или цифровой id.
4. Жми <b>👁 Группы</b> — листай кнопками.

<b>Команды</b>
/start — меню
/help — эта справка
/id — мой Telegram ID
/stats — состояние базы
/find <i>слово</i> — поиск по каталогу
/targets — все цели
/scan — перепроверить последнюю цель
/links — ссылки на чаты цели
/activity — собрать сообщения и связи
/network — топ собеседников
/feed — что пишет цель
/menu — карточка последней цели

<i>Только публичные данные. Не используй для слежки за людьми без согласия.</i>"""

EMPTY_DB = """\
<b>🗂 База пока пуста</b>

Это нормально для первого запуска. Порядок действий:

1. <code>healdragg session add main</code> — подключи свой аккаунт.
2. <code>healdragg bootstrap</code> — соберу все публичные чаты из диалогов
   и по ключевым словам.
3. Пришли id цели — и я проверю её участие по каталогу.

Кнопка <b>🚀 Собрать базу</b> запускает шаг 2 прямо здесь."""


def render_welcome(*, sessions: int, groups: int, targets: int) -> str:
    if groups == 0:
        state = "база пуста — нажми 🚀, чтобы собрать публичные чаты"
    else:
        state = f"в базе {grouped_int(groups)} чатов"
    return (
        f"{WELCOME}\n\n"
        f"├ 🔑 сессий: {sessions}\n"
        f"├ 🗂 {state}\n"
        f"└ 👥 целей: {targets}"
    )


def render_target_card(target: dict[str, Any], stats: dict[str, Any], *, tz: str = "Europe/Moscow") -> str:
    return render_profile(target, stats, tz=tz)


def render_scan_report(
    *,
    kind: str,
    stats: dict[str, Any],
    elapsed: float | None = None,
    errors: int = 0,
) -> str:
    icons = {"bootstrap": "🚀", "probe": "🔎", "dialogs": "🗂", "search": "🌐"}
    lines = [
        f"{icons.get(kind, '✅')} <b>Готово</b>",
        f"├ проверено: <b>{grouped_int(stats.get('checked', 0))}</b>"
        f" · новых: <b>{grouped_int(stats.get('found', 0))}</b>",
    ]
    if stats.get("joined"):
        lines.append(f"├ вступлений: {grouped_int(stats['joined'])}")
    if stats.get("skipped"):
        lines.append(f"├ пропущено: {grouped_int(stats['skipped'])}")
    if errors:
        lines.append(f"├ ошибок: {errors}")
    if elapsed is not None:
        from .util import human_duration

        lines.append(f"└ ⏱ {human_duration(elapsed)}")
    return "\n".join(lines)


__all__ = [
    "ChatRow",
    "chat_rows",
    "render_account_chats",
    "fit_telegram",
    "to_plain",
    "render_profile",
    "render_target_card",
    "render_stats",
    "render_catalog",
    "render_group_line",
    "render_scan_progress",
    "render_scan_report",
    "render_welcome",
    "progress_bar",
    "grouped_int",
    "WELCOME",
    "HELP",
    "EMPTY_DB",
    "Page",
]
