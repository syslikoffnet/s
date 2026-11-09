"""Генерирует SVG-превью для README: панель, список чатов, сообщение бота.

Запуск:  python scripts/make_preview.py
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich import box  # noqa: E402
from rich.console import Console, Group  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.text import Text  # noqa: E402

from healdragg.db import Store  # noqa: E402
from healdragg.demo import seed_demo  # noqa: E402
from healdragg.textfmt import render_account_chats  # noqa: E402
from healdragg.theme import THEME  # noqa: E402
from healdragg.tui import UiState, build_screen  # noqa: E402
from healdragg.util import Page  # noqa: E402

OUT = ROOT / "docs"
OUT.mkdir(exist_ok=True)


def demo_store() -> Store:
    tmp = Path(tempfile.mkdtemp(prefix="healdragg-preview-"))
    store = Store(tmp / "demo.db")
    seed_demo(store, total=115)
    return store


def make_console(width: int, height: int) -> Console:
    return Console(record=True, file=io.StringIO(), theme=THEME, width=width, height=height, color_system="truecolor")


def save(console: Console, path: Path, title: str) -> None:
    console.save_svg(str(path), title=title)
    print(f"✓ {path.relative_to(ROOT)}")


def preview_dashboard(store: Store) -> None:
    console = make_console(102, 42)
    console.print(build_screen(store, UiState(), console))
    save(console, OUT / "preview.svg", "healdragg tui")


def preview_groups(store: Store) -> None:
    console = make_console(102, 42)
    console.print(build_screen(store, UiState(mode="groups"), console))
    save(console, OUT / "groups.svg", "healdragg groups")


def preview_bot(store: Store) -> None:
    target = store.get_target(7126917488)
    total = store.count_target_groups(int(target["id"]))
    page = Page.build(total, 1, 15)
    rows = store.list_target_groups(int(target["id"]), page=1, size=15)
    message = render_account_chats(rows, target_id=7126917488, page=page)

    def buttons(*labels: str) -> Text:
        line = Text()
        for label in labels:
            line.append(f"  {label}  ", style="bold #d7e7ff on #2a2a3d")
            line.append(" ")
        return line

    console = make_console(74, 32)
    body = Group(
        Text("🤖 @healdragg_bot", style="muted"),
        Text(""),
        Text(message, style="#f2f2f7"),
        Text(""),
        buttons("👁 Группы"),
        buttons("🔄 Обновить", "📊 Профиль", "🏠 Меню"),
        buttons("· 1/8 ·", "➡ 2"),
    )
    console.print(
            Panel(
                body,
                title="[accent]Telegram[/accent]",
                title_align="left",
                box=box.ROUNDED,
                border_style="#50fa7b",
                padding=(1, 2),
            )
        )
    save(console, OUT / "bot.svg", "healdragg bot")


def main() -> None:
    store = demo_store()
    try:
        preview_dashboard(store)
        preview_groups(store)
        preview_bot(store)
    finally:
        store.close()


if __name__ == "__main__":
    main()
