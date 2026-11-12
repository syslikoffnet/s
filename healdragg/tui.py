"""Живая панель Healdragg для терминала (Linux, macOS, Termux).

Управление: ←/→ страницы, g — список чатов цели, b — собрать базу,
r — обновить, d — демо-данные, q — выход.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from rich import box
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import Settings
from .db import Store
from .scan import bootstrap, scan_target
from .textfmt import ChatRow, grouped_int, render_account_chats
from .theme import get_console as get_theme_console
from .util import Page, fmt_dt, fmt_relative, human_size, plural, truncate

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def get_console() -> Console:
    return get_theme_console()


# ── клавиатура ─────────────────────────────────────────────────────────────
class Keyboard:
    """Неблокирующее чтение клавиш (cbreak). В не-TTY просто выключено."""

    def __init__(self) -> None:
        self.enabled = False
        self._fd: int | None = None
        self._saved: list[Any] | None = None
        self._flags: int | None = None

    def __enter__(self) -> Keyboard:
        if not sys.stdin or not sys.stdin.isatty():
            return self
        try:
            import fcntl
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self._flags = fcntl.fcntl(self._fd, fcntl.F_GETFL)
            fcntl.fcntl(self._fd, fcntl.F_SETFL, self._flags | os_nonblock())
            self.enabled = True
        except Exception:  # noqa: BLE001
            self.enabled = False
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._fd is None or self._saved is None:
            return
        with contextlib.suppress(Exception):
            import fcntl
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            if self._flags is not None:
                fcntl.fcntl(self._fd, fcntl.F_SETFL, self._flags)
        self.enabled = False

    def read(self) -> str | None:
        if not self.enabled:
            return None
        try:
            import select

            ready, _, _ = select.select([sys.stdin], [], [], 0)
            if not ready:
                return None
            char = sys.stdin.read(1)
        except Exception:  # noqa: BLE001
            return None
        if char == "\x1b":
            try:
                import select

                ready, _, _ = select.select([sys.stdin], [], [], 0.02)
                seq = sys.stdin.read(2) if ready else ""
            except Exception:  # noqa: BLE001
                seq = ""
            return {"[D": "left", "[C": "right", "[A": "up", "[B": "down"}.get(seq, "esc")
        return char


def os_nonblock() -> int:
    import os

    return os.O_NONBLOCK


# ── состояние ──────────────────────────────────────────────────────────────
@dataclass
class UiState:
    target_idx: int = 0
    page: int = 1
    mode: str = "dashboard"  # dashboard | groups
    tick: int = 0
    job: str | None = None
    job_state: dict[str, Any] = field(default_factory=dict)
    last_error: str | None = None
    started_at: float = field(default_factory=time.time)


# ── блоки ──────────────────────────────────────────────────────────────────
def header(stats: dict[str, Any], state: UiState) -> Panel:
    online = "● live" if state.job else "○ idle"
    dot = "good" if state.job else "muted"
    title = Text()
    title.append("  🩺 HEALDRAGG  ", style="brand")
    title.append("каталог публичных чатов Telegram", style="muted")
    title.append(f"   {online}", style=dot)
    if state.job:
        spinner = SPINNER[state.tick % len(SPINNER)]
        title.append(f"  {spinner} ", style="accent")
        title.append(state.job_state.get("phase", "работаю"), style="accent")
    groups_n = int(stats.get("groups") or 0)
    targets_n = int(stats.get("targets") or 0)
    sessions_n = int(stats.get("sessions") or 0)
    subtitle = Text(
        f"{grouped_int(groups_n)} {plural(groups_n, ('группа', 'группы', 'групп'))} · "
        f"{grouped_int(targets_n)} {plural(targets_n, ('цель', 'цели', 'целей'))} · "
        f"{grouped_int(sessions_n)} {plural(sessions_n, ('сессия', 'сессии', 'сессий'))} · "
        f"{human_size(stats.get('db_bytes') or 0)}",
        style="muted",
    )
    body = Text.assemble(title, "\n", subtitle)
    return Panel(
        Align.center(body),
        box=box.ROUNDED,
        border_style="#bd93f9",
        padding=(0, 1),
        height=4,
    )


def sessions_panel(store: Store) -> Panel:
    rows = store.list_sessions()
    lines: list[Text] = []
    if not rows:
        lines.append(Text("нет сессий", style="warn"))
        lines.append(Text("healdragg session add main", style="muted"))
    for row in rows[:5]:
        status = row.get("status") or "?"
        icon = {"active": "●", "demo": "◌", "needs_login": "○", "banned": "✖"}.get(status, "○")
        style = {"active": "good", "demo": "accent", "banned": "bad"}.get(status, "muted")
        line = Text()
        line.append(f"{icon} ", style=style)
        line.append(truncate(row.get("name") or "?", 10), style="title")
        who = row.get("username") and f"@{row['username']}" or (str(row.get("tg_user_id") or ""))
        line.append(f" {who}", style="muted")
        line.append(f" · {row.get('groups_count', 0)}", style="count")
        lines.append(line)
    return Panel(
        Group(*lines),
        title="[accent]сессии[/accent]",
        title_align="left",
        box=box.ROUNDED,
        border_style="frame",
        padding=(0, 1),
        height=9,
    )


def db_panel(stats: dict[str, Any]) -> Panel:
    def line(label: str, value: str, style: str = "title") -> Text:
        t = Text()
        t.append(f"{label:<12}", style="muted")
        t.append(value, style=style)
        return t

    lines = [
        line("публичных", grouped_int(stats.get("public_groups")), "accent"),
        line("с @username", grouped_int(stats.get("with_username")), "accent"),
        line("покрытие", f"{grouped_int(stats.get('pairs'))} пар", "count"),
        line("связей", grouped_int(stats.get("target_groups")), "count"),
        line("размер", human_size(stats.get("db_bytes") or 0), "good"),
        line("обновлено", fmt_relative(stats.get("updated_at")), "muted"),
    ]
    return Panel(
        Group(*lines),
        title="[accent]база[/accent]",
        title_align="left",
        box=box.ROUNDED,
        border_style="frame",
        padding=(0, 1),
        height=9,
    )


def targets_panel(store: Store, state: UiState, *, rows_limit: int = 6) -> Panel:
    targets = store.list_targets(limit=10)
    if not targets:
        body = Align.center(
            Group(
                Text("цель не выбрана", style="warn"),
                Text(""),
                Text("пришли id или @username боту,", style="muted"),
                Text("или добавь в терминале:", style="muted"),
                Text("healdragg target add 7126917488", style="accent"),
            )
        )
        return Panel(
            body,
            title="[accent]карточка цели[/accent]",
            title_align="left",
            box=box.ROUNDED,
            border_style="frame",
            height=11,
        )

    state.target_idx = max(0, min(state.target_idx, len(targets) - 1))
    target = targets[state.target_idx]
    stats = store.target_stats(int(target["id"]))
    total = int(stats.get("total") or 0)
    page = Page.build(total, state.page, max(1, rows_limit))
    state.page = page.page

    name = " ".join(p for p in (target.get("first_name"), target.get("last_name")) if p)
    who = f"@{target['username']}" if target.get("username") else "без username"
    head = Text()
    head.append(f"👤 {truncate(name or who, 30)} ", style="title")
    head.append(f"· {who} ", style="muted")
    head.append(f"· {target.get('tg_user_id')}", style="accent")

    meta = Text()
    meta.append(f"💬 {total} чатов ", style="count")
    meta.append(f"👮 {stats.get('admin', 0)} ", style="warn")
    meta.append(f"🔒 {stats.get('private', 0)} ", style="accent")
    meta.append(f"✖️ {stats.get('left_count', 0)} ", style="muted")
    meta.append(f"· {fmt_dt(stats.get('updated_at'), 'Europe/Moscow')}", style="muted")

    body: list[RenderableType] = [head, meta, Text("")]
    if total == 0:
        body.append(Text("пока нет данных — нажми b (собрать базу) или r (проверить)", style="warn"))
        for row in store.list_groups(page=1, size=rows_limit - 1):
            body.append(group_line(row, target.get("last_scan_at")))
    else:
        rows = store.list_target_groups(int(target["id"]), page=page.page, size=page.size)
        for row in rows:
            body.append(group_line(row, target.get("last_scan_at")))
        body.append(Text(""))
        nav = Text()
        nav.append(f"  стр. {page.page}/{page.pages}", style="muted")
        nav.append("   ←/→ листать · g полный экран", style="muted")
        body.append(nav)

    return Panel(
        Group(*body),
        title=f"[accent]карточка цели {state.target_idx + 1}/{len(targets)}[/accent]",
        title_align="left",
        box=box.ROUNDED,
        border_style="frame",
        padding=(0, 1),
        height=11,
    )


def group_line(row: dict[str, Any] | ChatRow, tz_ts: Any = None) -> Text:
    from .util import fmt_day

    chat = row if isinstance(row, ChatRow) else ChatRow.from_mapping(row)
    line = Text()
    if chat.markers:
        line.append(chat.markers, style="warn")
    line.append(f"{fmt_day(chat.last_message_at)} ", style="date")
    line.append(truncate(chat.title, 34), style="title")
    if chat.messages_total is not None:
        line.append(f" ({chat.messages_total})", style="count")
    return line


def log_panel(store: Store, *, height: int = 8) -> Panel:
    events = store.recent_events(max(2, height - 3))
    lines: list[Text] = []
    if not events:
        lines.append(Text("журнал пуст", style="muted"))
    for event in events:
        style = {"error": "bad", "warn": "warn"}.get(event.get("level"), "muted")
        stamp = time.strftime("%H:%M:%S", time.localtime(int(event.get("ts") or 0)))
        t = Text()
        t.append(f"{stamp} ", style="date")
        t.append(truncate(event.get("message") or "", 74), style=style)
        lines.append(t)
    return Panel(
        Group(*lines),
        title="[accent]журнал[/accent]",
        title_align="left",
        box=box.ROUNDED,
        border_style="frame",
        padding=(0, 1),
        height=height,
    )


def footer(state: UiState) -> Panel:
    text = Text()
    if state.mode == "groups":
        text.append(" ←/→ страницы   ", style="accent")
        text.append("g/esc назад   ", style="accent")
        text.append("q выход", style="accent")
    else:
        for key, label in (
            ("←/→", "страницы"),
            ("↑/↓", "цель"),
            ("g", "группы"),
            ("b", "собрать базу"),
            ("r", "проверить"),
            ("d", "демо"),
            ("q", "выход"),
        ):
            text.append(f" {key} ", style="good")
            text.append(f"{label}  ", style="muted")
    return Panel(Align.center(text), box=box.ROUNDED, border_style="frame", padding=(0, 0))


def groups_screen(store: Store, state: UiState, *, height: int) -> RenderableType:
    targets = store.list_targets(limit=10)
    if not targets:
        return Panel(
            Align.center(Text("Сначала добавь цель: healdragg target add <id>", style="warn")),
            box=box.ROUNDED,
            border_style="#bd93f9",
        )
    target = targets[max(0, min(state.target_idx, len(targets) - 1))]
    total = store.count_target_groups(int(target["id"]))
    size = max(4, height - 10)
    page = Page.build(total, state.page, size)
    state.page = page.page
    rows = store.list_target_groups(int(target["id"]), page=page.page, size=page.size)
    text = render_account_chats(
        rows,
        target_id=target["tg_user_id"],
        page=page,
        title_limit=40,
        homoglyph=True,
    )
    return Panel(
        Text(text, style="#f8f8f2"),
        title=f"[accent]👁 группы · {target['tg_user_id']}[/accent]",
        title_align="left",
        box=box.ROUNDED,
        border_style="#bd93f9",
        padding=(1, 2),
    )


def two_columns(left: RenderableType, right: RenderableType) -> RenderableType:
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(left, right)
    return grid


def build_screen(store: Store, state: UiState, console: Console) -> RenderableType:
    stats = store.stats()
    if state.mode == "groups":
        return Group(
            header(stats, state),
            groups_screen(store, state, height=max(10, console.size.height - 4)),
            footer(state),
        )
    height = console.size.height
    log_height = max(5, min(8, height - 26))
    return Group(
        header(stats, state),
        two_columns(sessions_panel(store), db_panel(stats)),
        targets_panel(store, state),
        log_panel(store, height=log_height),
        footer(state),
    )


# ── запуск ─────────────────────────────────────────────────────────────────
async def run_tui(settings: Settings, store: Store | None = None, *, once: bool = False) -> None:
    console = get_console()
    own_store = store is None
    store = store or Store(settings.db_path)
    store.cancel_stale_scans()
    state = UiState()

    if once:
        console.print(build_screen(store, state, console))
        if own_store:
            store.close()
        return

    from .tg import SessionPool

    pool = SessionPool(settings, store)
    tasks: dict[str, asyncio.Task] = {}

    def busy() -> bool:
        return any(not t.done() for t in tasks.values())

    async def start_job(kind: str) -> None:
        if busy():
            return
        state.job = kind

        async def on_progress(progress: dict[str, Any]) -> None:
            state.job_state = progress

        async def runner() -> None:
            try:
                if kind == "bootstrap":
                    await bootstrap(
                        pool,
                        store,
                        settings,
                        join=settings.bootstrap_join,
                        join_limit=settings.join_limit,
                        reporter=on_progress,
                    )
                elif kind == "target":
                    targets = store.list_targets(limit=10)
                    if targets:
                        target = targets[max(0, min(state.target_idx, len(targets) - 1))]
                        await scan_target(pool, store, settings, target, reporter=on_progress)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                state.last_error = str(exc)
                store.log_event("error", str(exc), "tui")
            finally:
                state.job = None
                state.job_state = {}

        tasks[kind] = asyncio.create_task(runner(), name=kind)
        tasks[kind].add_done_callback(lambda t, k=kind: tasks.pop(k, None))

    try:
        with Keyboard() as keyboard:
            with Live(
                build_screen(store, state, console),
                console=console,
                screen=True,
                refresh_per_second=6,
                vertical_overflow="crop",
            ) as live:
                while True:
                    state.tick += 1
                    live.update(build_screen(store, state, console))
                    key = keyboard.read()
                    if key:
                        if key in {"q", "Q"}:
                            break
                        if key == "esc" and state.mode == "groups":
                            state.mode = "dashboard"
                        elif key == "g":
                            state.mode = "groups" if state.mode == "dashboard" else "dashboard"
                        elif key == "left":
                            state.page = max(1, state.page - 1)
                        elif key == "right":
                            state.page += 1
                        elif key == "up":
                            state.target_idx = max(0, state.target_idx - 1)
                            state.page = 1
                        elif key == "down":
                            state.target_idx += 1
                            state.page = 1
                        elif key == "r":
                            await start_job("target")
                        elif key == "b":
                            await start_job("bootstrap")
                        elif key == "d":
                            from .demo import seed_demo

                            seed_demo(store)
                            store.log_event("info", "демо-данные загружены", "tui")
                        elif key in {"\x03"}:
                            break
                    if not keyboard.enabled:
                        await asyncio.sleep(0.5)
                    else:
                        await asyncio.sleep(0.12)
    except KeyboardInterrupt:
        pass
    finally:
        for task in tasks.values():
            task.cancel()
        await pool.close()
        if own_store:
            store.close()


def main() -> None:
    from .config import load_settings
    from .log import setup_logging

    settings = load_settings()
    setup_logging(settings.log_level)
    try:
        asyncio.run(run_tui(settings))
    except KeyboardInterrupt:
        pass


__all__ = ["run_tui", "main", "build_screen", "UiState", "get_console"]
