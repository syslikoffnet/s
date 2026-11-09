"""CLI Healdragg.

    healdragg init                 # .env + база
    healdragg session add main     # вход в аккаунт
    healdragg bootstrap            # собрать каталог публичных чатов
    healdragg target add 7126917488
    healdragg scan 7126917488      # проверить участие по каталогу
    healdragg groups 7126917488    # красивый список с листалкой
    healdragg bot                  # @healdragg_bot
    healdragg api                  # панель для Android
    healdragg tui                  # живая панель в терминале
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich import box
from rich.align import Align
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .config import Settings, load_settings
from .db import Store
from .log import setup_logging
from .textfmt import (
    EMPTY_DB,
    grouped_int,
    render_account_chats,
    render_profile,
    render_scan_report,
    render_stats,
    to_plain,
)
from .theme import get_console
from .util import Page, fmt_dt, fmt_relative, human_size, truncate

console = get_console()
err = get_console(stderr=True)

BANNER = r"""
  ██╗  ██╗███████╗ █████╗ ██╗     ██████╗ ██████╗  █████╗  ██████╗  ██████╗
  ██║  ██║██╔════╝██╔══██╗██║     ██╔══██╗██╔══██╗██╔══██╗██╔════╝ ██╔════╝
  ███████║█████╗  ███████║██║     ██║  ██║██████╔╝███████║██║  ███╗██║  ███╗
  ██╔══██║██╔══╝  ██╔══██║██║     ██║  ██║██╔══██╗██╔══██║██║   ██║██║   ██║
  ██║  ██║███████╗██║  ██║███████╗██████╔╝██║  ██║██║  ██║╚██████╔╝╚██████╔╝
  ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚══════╝╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝  ╚═════╝
"""


# ── парсер ─────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="healdragg",
        description="Healdragg — каталог публичных групп Telegram, бот, TUI и панель.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "примеры:\n"
            "  healdragg demo\n"
            "  healdragg session add main\n"
            "  healdragg bootstrap --join --join-limit 10\n"
            "  healdragg cover @durov --join-limit 15\n"
            "  healdragg target add @durov\n"
            "  healdragg groups 7126917488 --interactive\n"
            "  healdragg bot\n"
        ),
    )
    parser.add_argument("-e", "--env", help="путь к .env")
    parser.add_argument("-v", "--version", action="version", version=f"healdragg {__version__}")
    parser.add_argument("--debug", action="store_true", help="подробные логи")

    sub = parser.add_subparsers(dest="command", metavar="команда")

    sub.add_parser("init", help="создать .env, каталоги и базу")

    p_demo = sub.add_parser("demo", help="залить демо-данные (без Telegram)")
    p_demo.add_argument("--clear", action="store_true", help="удалить демо-данные")
    p_demo.add_argument("--target", type=int, default=7126917488)
    p_demo.add_argument("--total", type=int, default=115)

    p_s = sub.add_parser("session", help="управление аккаунтами-сканерами")
    s_sub = p_s.add_subparsers(dest="session_cmd", metavar="действие")
    s_add = s_sub.add_parser("add", help="войти в аккаунт")
    s_add.add_argument("name", nargs="?", default="main")
    s_add.add_argument("--phone", help="номер в формате +7999…")
    s_add.add_argument("--relogin", action="store_true", help="перелогиниться, если сессия битая")
    s_add.add_argument("--qr", action="store_true", help="вход по QR-коду")
    s_sub.add_parser("list", help="список сессий")
    s_rm = s_sub.add_parser("remove", help="удалить сессию")
    s_rm.add_argument("name")
    s_sub.add_parser("import", help="подхватить *.session из папки data/sessions")

    p_b = sub.add_parser("bootstrap", help="собрать каталог публичных чатов")
    p_b.add_argument("--session", action="append", dest="sessions")
    p_b.add_argument("--no-meta", action="store_true", help="не запрашивать счётчики сообщений")
    p_b.add_argument("--join", action="store_true", help="вступать в найденные публичные чаты")
    p_b.add_argument("--join-limit", type=int, default=None)
    p_b.add_argument("--queries", nargs="*", help="слова для поиска чатов")

    p_t = sub.add_parser("target", help="цели наблюдения")
    t_sub = p_t.add_subparsers(dest="target_cmd", metavar="действие")
    t_add = t_sub.add_parser("add", help="добавить цель")
    t_add.add_argument("query")
    t_sub.add_parser("list", help="список целей")
    t_rm = t_sub.add_parser("remove", help="удалить цель")
    t_rm.add_argument("query")

    p_scan = sub.add_parser("scan", help="проверить участие цели по каталогу")
    p_scan.add_argument("query")
    p_scan.add_argument("--session", action="append", dest="sessions")
    p_scan.add_argument("--max-groups", type=int, default=None)
    p_scan.add_argument("--deep", action="store_true", help="глубже искать в участниках")
    p_scan.add_argument("--no-search", action="store_true", help="без глобального поиска")

    p_cov = sub.add_parser(
        "cover", help="с нуля: собрать базу, вступить в публичные чаты и проверить цель"
    )
    p_cov.add_argument("query")
    p_cov.add_argument("--no-join", action="store_true", help="не вступать в новые чаты")
    p_cov.add_argument("--join-limit", type=int, default=None, help="вступлений за сутки")
    p_cov.add_argument("--queries", nargs="*", help="слова для поиска чатов")
    p_cov.add_argument("--page", type=int, default=1)

    p_g = sub.add_parser("groups", help="список чатов цели")
    p_g.add_argument("query")
    p_g.add_argument("--page", type=int, default=1)
    p_g.add_argument("--size", type=int, default=None)
    p_g.add_argument("-i", "--interactive", action="store_true", help="листать стрелками")
    p_g.add_argument("--plain", action="store_true", help="без омоглифов")
    p_g.add_argument("--json", action="store_true", help="машинный вывод")
    p_g.add_argument("--html", action="store_true", help="текст для Telegram")

    p_c = sub.add_parser("catalog", help="каталог групп")
    p_c.add_argument("--page", type=int, default=1)
    p_c.add_argument("--size", type=int, default=30)
    p_c.add_argument("--query", help="поиск по названию/username")
    p_c.add_argument("--json", action="store_true")

    sub.add_parser("stats", help="статистика базы")
    sub.add_parser("doctor", help="проверка окружения")

    p_db = sub.add_parser("db", help="обслуживание базы")
    d_sub = p_db.add_subparsers(dest="db_cmd", metavar="действие")
    d_sub.add_parser("optimize", help="ANALYZE + checkpoint + incremental vacuum")
    d_sub.add_parser("vacuum", help="полное сжатие файла")
    d_sub.add_parser("check", help="проверка целостности")
    d_sub.add_parser("stats", help="размеры таблиц")
    d_exp = d_sub.add_parser("export", help="экспорт цели в JSON")
    d_exp.add_argument("query")
    d_exp.add_argument("-o", "--out", default="-")
    d_imp = d_sub.add_parser("import", help="импорт цели из JSON")
    d_imp.add_argument("path")

    p_bot = sub.add_parser("bot", help="запустить @healdragg_bot")
    p_bot.add_argument("--token", help="переопределить BOT_TOKEN")

    p_api = sub.add_parser("api", help="REST + веб-панель (для Android)")
    p_api.add_argument("--host", default=None)
    p_api.add_argument("--port", type=int, default=None)
    p_api.add_argument("--show-token", action="store_true", help="напечатать токен доступа")

    p_tui = sub.add_parser("tui", help="живая панель в терминале")
    p_tui.add_argument("--once", action="store_true", help="нарисовать один раз и выйти")
    return parser


# ── helpers ────────────────────────────────────────────────────────────────
def _settings(args: argparse.Namespace) -> Settings:
    settings = load_settings(args.env)
    if args.debug:
        settings.log_level = "DEBUG"
    setup_logging(settings.log_level)
    return settings


def _store(settings: Settings) -> Store:
    settings.ensure_dirs()
    return Store(settings.db_path)


def _resolve_target(store: Store, query: str) -> dict[str, Any] | None:
    return store.find_target(query)


async def _need_target(settings: Settings, store: Store, query: str) -> dict[str, Any]:
    target = store.find_target(query)
    if target:
        return target
    from .scan import resolve_and_store_target
    from .tg import SessionPool

    pool = SessionPool(settings, store)
    try:
        target = await resolve_and_store_target(pool, store, query)
        console.print(f"[good]✓[/] цель добавлена: [accent]{target['tg_user_id']}[/]")
        return target
    finally:
        await pool.close()


def _print_banner() -> None:
    console.print(Text(BANNER, style="bold #bd93f9"))
    console.print(
        Align.center(
            Text(
                f"каталог публичных чатов Telegram · v{__version__}",
                style="muted",
            )
        )
    )
    console.print()


def _print_target(target: dict[str, Any], stats: dict[str, Any], settings: Settings) -> None:
    console.print(
        Panel(
            Text(to_plain(render_profile(target, stats, tz=settings.tz)), style="#f8f8f2"),
            title="[accent]профиль[/accent]",
            title_align="left",
            box=box.ROUNDED,
            border_style="#bd93f9",
            padding=(0, 1),
        )
    )


def _groups_view(
    rows: Sequence[dict[str, Any]],
    target_id: int,
    page: Page,
    settings: Settings,
    *,
    homoglyph: bool = True,
) -> Panel:
    text = render_account_chats(
        rows,
        target_id=target_id,
        page=page,
        tz=settings.tz,
        title_limit=settings.title_limit,
        homoglyph=homoglyph,
    )
    return Panel(
        Text(text, style="#f8f8f2"),
        title=f"[accent]👁 группы · {target_id}[/accent]",
        title_align="left",
        box=box.ROUNDED,
        border_style="#bd93f9",
        padding=(1, 2),
    )


# ── команды ────────────────────────────────────────────────────────────────
def cmd_init(settings: Settings) -> int:
    settings.ensure_dirs()
    env_path = settings.env_file or (Path.cwd() / ".env")
    example = Path(__file__).resolve().parent.parent / ".env.example"

    if env_path.exists():
        console.print(f"[warn]•[/] .env уже есть: {env_path}")
    else:
        values: dict[str, str] = {}
        console.print("[accent]Настройка Healdragg[/]")
        console.print("API_ID и API_HASH: https://my.telegram.org → API development tools")
        console.print()

        def ask(label: str, secret: bool = False, default: str = "") -> str:
            try:
                raw = input(f"{label}{' [' + default + ']' if default else ''}: ").strip()
            except EOFError:
                raw = ""
            return raw or default

        values["API_ID"] = ask("API_ID")
        values["API_HASH"] = ask("API_HASH")
        values["BOT_TOKEN"] = ask("BOT_TOKEN (можно позже)")
        values["BOT_ALLOWED_USERS"] = ask("Твой Telegram id (можно позже, узнаешь в боте /id)")
        values["API_TOKEN"] = secrets.token_urlsafe(24)
        if example.exists():
            content = example.read_text(encoding="utf-8")
            for key, value in values.items():
                if value:
                    content = _set_env_line(content, key, value)
        else:
            content = "\n".join(f"{k}={v}" for k, v in values.items()) + "\n"
        env_path.write_text(content, encoding="utf-8")
        console.print(f"[good]✓[/] создан {env_path}")
        reloaded = load_settings(str(env_path))
        settings.api_token = reloaded.api_token or settings.api_token
        settings.api_id = reloaded.api_id
        settings.api_hash = reloaded.api_hash
        settings.bot_token = reloaded.bot_token
        settings.bot_allowed_users = reloaded.bot_allowed_users

    store = Store(settings.db_path)
    store.log_event("info", "база создана", "cli")
    store.optimize()
    store.close()
    console.print(f"[good]✓[/] база: [accent]{settings.db_path}[/]")
    console.print(f"[good]✓[/] сессии: [accent]{settings.sessions_dir}[/]")
    console.print(f"[good]✓[/] токен панели: [accent]{settings.api_token or '(см. .env)'}[/]")
    console.print()
    console.print("[accent]Дальше:[/]")
    console.print("  1. [bold]healdragg session add main[/]   — вход в аккаунт")
    console.print("  2. [bold]healdragg bootstrap[/]          — сбор публичных чатов")
    console.print("  3. [bold]healdragg bot[/]                — бот")
    console.print("  4. [bold]healdragg tui[/]                — панель")
    return 0


def _set_env_line(content: str, key: str, value: str) -> str:
    lines = content.splitlines()
    prefix = f"{key}="
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def cmd_demo(args: argparse.Namespace, settings: Settings) -> int:
    store = _store(settings)
    from .demo import clear_demo, seed_demo

    if args.clear:
        clear_demo(store)
        console.print("[good]✓[/] демо-данные удалены")
        store.close()
        return 0
    info = seed_demo(store, target_id=args.target, total=args.total)
    stats = store.target_stats(int(info["target_pk"]))
    _print_banner()
    console.print("[good]✓[/] демо-данные загружены")
    _print_target(info["target"], stats, settings)
    total = stats.get("total", 0)
    page = Page.build(total, 1, settings.page_size)
    rows = store.list_target_groups(int(info["target_pk"]), page=1, size=page.size)
    console.print(_groups_view(rows, args.target, page, settings))
    console.print("[muted]дальше:[/] healdragg tui  ·  healdragg groups 7126917488 -i  ·  healdragg bot")
    store.close()
    return 0


def cmd_session(args: argparse.Namespace, settings: Settings) -> int:
    action = args.session_cmd or "list"
    store = _store(settings)
    if action == "list":
        rows = store.list_sessions()
        if not rows:
            console.print("[warn]сессий нет[/] · добавь: [accent]healdragg session add main[/]")
            store.close()
            return 0
        table = Table(box=box.ROUNDED, border_style="#44475a", header_style="accent", expand=False)
        for col in ("имя", "аккаунт", "id", "статус", "чатов", "последний раз"):
            table.add_column(col)
        for row in rows:
            status = row.get("status") or "?"
            style = {"active": "good", "demo": "accent", "banned": "bad"}.get(status, "warn")
            table.add_row(
                f"[bold]{row['name']}[/]",
                f"@{row['username']}" if row.get("username") else "—",
                str(row.get("tg_user_id") or "—"),
                f"[{style}]{status}[/]",
                str(row.get("groups_count") or 0),
                fmt_relative(row.get("last_used_at"), settings.tz),
            )
        console.print(table)
        store.close()
        return 0

    if action == "remove":
        store.remove_session(args.name)
        try:
            settings.session_file(args.name).unlink(missing_ok=True)
        except OSError:
            pass
        console.print(f"[good]✓[/] сессия [accent]{args.name}[/] удалена")
        store.close()
        return 0

    if action == "import":
        from .tg import session_files

        found = session_files(settings.sessions_dir)
        for name in found:
            store.add_session(name, str(settings.session_file(name)), status="active")
        console.print(f"[good]✓[/] найдено сессий: {len(found)} → {settings.sessions_dir}")
        store.close()
        return 0

    if action == "add":
        return asyncio.run(_add_session(args, settings, store))

    store.close()
    return 0


async def _add_session(args: argparse.Namespace, settings: Settings, store: Store) -> int:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError

    api_id, api_hash = settings.require_api()
    path = settings.session_file(args.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not args.relogin:
        console.print(f"[warn]•[/] сессия уже есть: {path} (перелогин: --relogin)")

    client = TelegramClient(
        str(path),
        api_id,
        api_hash,
        device_model="Healdragg",
        system_version="Android 14",
        app_version="1.0.0",
        lang_code="ru",
        system_lang_code="ru",
    )
    await client.connect()
    try:
        if await client.is_user_authorized() and not args.relogin:
            me = await client.get_me()
            console.print(
                f"[good]✓[/] сессия [accent]{args.name}[/] уже активна: "
                f"{me.first_name} (@{me.username}) id={me.id}"
            )
        else:
            if args.relogin:
                try:
                    await client.log_out()
                except Exception:  # noqa: BLE001
                    pass
                await client.connect()
            console.print("[accent]Вход в аккаунт[/] — код придёт в Telegram.")
            if args.qr:
                await _qr_login(client, console)
            else:
                phone = args.phone or input("номер (+7999…): ").strip()
                await client.send_code_request(phone)
                code = input("код из Telegram: ").strip()
                try:
                    await client.sign_in(phone=phone, code=code)
                except SessionPasswordNeededError:
                    password = input("пароль 2FA: ").strip()
                    await client.sign_in(password=password)
            me = await client.get_me()
            console.print(f"[good]✓[/] вошли как {me.first_name} (@{me.username}) id={me.id}")

        store.add_session(
            args.name,
            str(path),
            tg_user_id=me.id,
            username=me.username,
            first_name=me.first_name,
            phone=getattr(me, "phone", None),
            status="active",
        )
        store.log_event("info", f"сессия {args.name} подключена", "cli")
        console.print("[muted]подсказка:[/] healdragg bootstrap — чтобы наполнить базу")
        return 0
    finally:
        await client.disconnect()


async def _qr_login(client, out: Any) -> None:
    qr = await client.qr_login()
    out.print("[accent]Открой Telegram → Настройки → Устройства → Подключить по QR[/]")
    try:
        import qrcode  # type: ignore

        qr_obj = qrcode.QRCode(border=1)
        qr_obj.add_data(qr.url)
        qr_obj.print_ascii(invert=True)
    except Exception:  # noqa: BLE001
        out.print(f"[accent]ссылка:[/] {qr.url}")
    await qr.wait()


def cmd_bootstrap(args: argparse.Namespace, settings: Settings) -> int:
    return asyncio.run(_bootstrap(args, settings))


async def _bootstrap(args: argparse.Namespace, settings: Settings) -> int:
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    from .scan import bootstrap
    from .tg import SessionPool

    store = _store(settings)
    pool = SessionPool(settings, store)
    progress = Progress(
        SpinnerColumn(style="accent"),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=30, style="grey30", complete_style="accent"),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    try:
        with progress:
            task = progress.add_task("подключаюсь…", total=0)

            async def on_progress(state: dict[str, Any]) -> None:
                total = int(state.get("total") or 0)
                progress.update(
                    task,
                    description=str(state.get("phase") or "собираю"),
                    total=total or None,
                    completed=int(state.get("done") or 0),
                )

            result = await bootstrap(
                pool,
                store,
                settings,
                sessions=args.sessions,
                queries=args.queries,
                join=args.join,
                join_limit=args.join_limit,
                fetch_meta=not args.no_meta,
                reporter=on_progress,
            )
        console.print(
            Panel(
                Text(
                    to_plain(
                        render_scan_report(
                            kind="bootstrap",
                            stats={"checked": result.get("checked", 0), "found": result.get("groups", 0)},
                            elapsed=result.get("elapsed"),
                        )
                    ),
                    style="#f8f8f2",
                ),
                box=box.ROUNDED,
                border_style="#50fa7b",
                title="[good]каталог[/good]",
            )
        )
        console.print(
            Text(to_plain(render_stats(store.stats(), tz=settings.tz)), style="#f8f8f2")
        )
        return 0
    except KeyboardInterrupt:
        err.print("[warn]прервано[/]")
        return 130
    finally:
        await pool.close()
        store.close()


def cmd_target(args: argparse.Namespace, settings: Settings) -> int:
    action = args.target_cmd or "list"
    store = _store(settings)
    if action == "list":
        rows = store.list_targets(50)
        if not rows:
            console.print("[warn]целей нет[/] · healdragg target add 7126917488")
            store.close()
            return 0
        table = Table(box=box.ROUNDED, border_style="#44475a", header_style="accent")
        for col in ("id", "имя", "username", "чатов", "в базе", "обновлено"):
            table.add_column(col)
        for row in rows:
            stats = store.target_stats(int(row["id"]))
            table.add_row(
                str(row["tg_user_id"]),
                truncate(" ".join(p for p in (row.get("first_name"), row.get("last_name")) if p) or "—", 22),
                f"@{row['username']}" if row.get("username") else "—",
                str(stats.get("total", 0)),
                str(store.count_target_groups(int(row["id"]), "member")),
                fmt_relative(row.get("last_scan_at"), settings.tz),
            )
        console.print(table)
        store.close()
        return 0
    if action == "remove":
        target = _resolve_target(store, args.query)
        if not target:
            console.print("[bad]не найдено[/]")
            store.close()
            return 1
        store.delete_target(int(target["tg_user_id"]))
        console.print("[good]✓[/] цель удалена")
        store.close()
        return 0
    if action == "add":
        return asyncio.run(_target_add(args.query, settings, store))
    store.close()
    return 0


async def _target_add(query: str, settings: Settings, store: Store) -> int:
    from .scan import resolve_and_store_target
    from .tg import SessionPool

    pool = SessionPool(settings, store)
    try:
        target = await resolve_and_store_target(pool, store, query)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[bad]✗ не удалось:[/] {exc}")
        return 1
    finally:
        await pool.close()
    stats = store.target_stats(int(target["id"]))
    _print_target(target, stats, settings)
    console.print(f"[muted]дальше:[/] healdragg scan {target['tg_user_id']}")
    store.close()
    return 0


def cmd_scan(args: argparse.Namespace, settings: Settings) -> int:
    return asyncio.run(_scan(args, settings))


async def _scan(args: argparse.Namespace, settings: Settings) -> int:
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    from .scan import scan_target
    from .tg import SessionPool

    store = _store(settings)
    target = store.find_target(args.query)
    if not target:
        console.print(f"[bad]цель «{args.query}» не найдена[/] · healdragg target add {args.query}")
        store.close()
        return 1
    pool = SessionPool(settings, store)
    progress = Progress(
        SpinnerColumn(style="accent"),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=30, style="grey30", complete_style="accent"),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    try:
        with progress:
            task = progress.add_task("стартую…", total=0)

            async def on_progress(state: dict[str, Any]) -> None:
                progress.update(
                    task,
                    description=str(state.get("phase") or "проверяю"),
                    total=int(state.get("total") or 0) or None,
                    completed=int(state.get("done") or 0),
                )

            result = await scan_target(
                pool,
                store,
                settings,
                target,
                sessions=args.sessions,
                max_groups=args.max_groups,
                deep=args.deep,
                reporter=on_progress,
                use_global_search=not args.no_search,
            )
        stats = store.target_stats(int(target["id"]))
        console.print(
            Panel(
                Text(
                    to_plain(
                        render_scan_report(
                            kind="probe",
                            stats={"checked": result.get("checked", 0), "found": stats.get("total", 0)},
                            elapsed=result.get("elapsed"),
                            errors=int(result.get("errors", 0)),
                        )
                    ),
                    style="#f8f8f2",
                ),
                box=box.ROUNDED,
                border_style="#50fa7b",
            )
        )
        _print_target(target, stats, settings)
        console.print(f"[muted]дальше:[/] healdragg groups {target['tg_user_id']} -i")
        return 0
    except KeyboardInterrupt:
        err.print("[warn]прервано[/]")
        return 130
    finally:
        await pool.close()
        store.close()


def cmd_cover(args: argparse.Namespace, settings: Settings) -> int:
    return asyncio.run(_cover(args, settings))


async def _cover(args: argparse.Namespace, settings: Settings) -> int:
    """Одна команда для пустой базы: собрать → (вступить) → проверить → показать."""
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    from .scan import bootstrap, resolve_and_store_target, scan_target
    from .tg import SessionPool

    store = _store(settings)
    pool = SessionPool(settings, store)
    progress = Progress(
        SpinnerColumn(style="accent"),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=28, style="grey30", complete_style="accent"),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    try:
        try:
            target = store.find_target(args.query)
        except Exception:  # noqa: BLE001
            target = None
        if not target:
            try:
                target = await resolve_and_store_target(pool, store, args.query)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[bad]✗ не удалось найти цель:[/] {exc}")
                return 1
        console.print(
            f"[accent]🎯 цель:[/] {target['tg_user_id']} "
            f"({target.get('username') and '@' + target['username'] or 'без username'})"
        )

        with progress:
            task = progress.add_task("собираю базу…", total=0)

            async def on_progress(state: dict[str, Any]) -> None:
                progress.update(
                    task,
                    description=str(state.get("phase") or "работаю"),
                    total=int(state.get("total") or 0) or None,
                    completed=int(state.get("done") or 0),
                )

            boot = await bootstrap(
                pool,
                store,
                settings,
                queries=args.queries,
                join=not args.no_join,
                join_limit=args.join_limit,
                fetch_meta=False,
                reporter=on_progress,
            )
            progress.update(task, description="проверяю участие", total=None, completed=0)
            result = await scan_target(pool, store, settings, target, reporter=on_progress)

        stats = store.target_stats(int(target["id"]))
        console.print(
            Panel(
                Text(
                    to_plain(
                        f"🚀 собрано чатов: <b>{boot.get('groups', 0)}</b>"
                        f" · вступлений: {boot.get('joined', 0)}\n"
                        f"🔎 проверено: {result.get('checked', 0)}"
                        f" · найдено у цели: <b>{stats.get('total', 0)}</b>"
                    )
                ),
                box=box.ROUNDED,
                border_style="#50fa7b",
            )
        )
        _print_target(target, stats, settings)
        total = int(stats.get("total") or 0)
        if total:
            page = Page.build(total, args.page, settings.page_size)
            rows = store.list_target_groups(int(target["id"]), page=page.page, size=page.size)
            console.print(_groups_view(rows, int(target["tg_user_id"]), page, settings))
        else:
            console.print(
                "[warn]Цель не найдена ни в одном покрытом чате.[/]\n"
                "[muted]Попробуй: больше сессий, --queries со словами по теме цели, "
                "или добавь сессию самой цели.[/]"
            )
        return 0
    except KeyboardInterrupt:
        err.print("[warn]прервано[/]")
        return 130
    finally:
        await pool.close()
        store.close()


def cmd_groups(args: argparse.Namespace, settings: Settings) -> int:
    store = _store(settings)
    target = store.find_target(args.query)
    if not target:
        console.print(f"[bad]цель «{args.query}» не найдена[/] · healdragg target add {args.query}")
        store.close()
        return 1
    stats = store.target_stats(int(target["id"]))
    total = int(stats.get("total") or 0)
    size = args.size or settings.page_size

    if args.json:
        payload = {
            "target": target,
            "stats": stats,
            "page": args.page,
            "size": size,
            "groups": store.list_target_groups(int(target["id"]), page=args.page, size=size),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        store.close()
        return 0

    if total == 0:
        console.print(
            Panel(
                Text.from_markup(
                    f"[warn]У цели пока нет чатов.[/]\n\n"
                    f"Собери базу: [accent]healdragg bootstrap[/]\n"
                    f"Проверь цель: [accent]healdragg scan {target['tg_user_id']}[/]"
                ),
                border_style="#f1fa8c",
                box=box.ROUNDED,
            )
        )
        store.close()
        return 0

    if args.html:
        page = Page.build(total, args.page, size)
        text = render_account_chats(
            store.list_target_groups(int(target["id"]), page=page.page, size=size),
            target_id=int(target["tg_user_id"]),
            page=page,
            tz=settings.tz,
            title_limit=settings.title_limit,
            homoglyph=not args.plain,
            escape_html=True,
        )
        print(text)
        store.close()
        return 0

    if not args.interactive:
        page = Page.build(total, args.page, size)
        rows = store.list_target_groups(int(target["id"]), page=page.page, size=size)
        console.print(_print_banner_only())
        _print_target(target, stats, settings)
        console.print(_groups_view(rows, int(target["tg_user_id"]), page, settings, homoglyph=not args.plain))
        return 0

    return _interactive_groups(store, target, settings, size, args.plain)


def _print_banner_only() -> Text:
    return Text(BANNER, style="bold #bd93f9")


def _interactive_groups(
    store: Store,
    target: dict[str, Any],
    settings: Settings,
    size: int,
    plain: bool,
) -> int:
    from rich.live import Live

    from .tui import Keyboard

    total = store.count_target_groups(int(target["id"]))
    page_num = 1

    def render() -> RenderableType:
        nonlocal page_num
        page = Page.build(total, page_num, size)
        page_num = page.page
        rows = store.list_target_groups(int(target["id"]), page=page.page, size=page.size)
        body = _groups_view(rows, int(target["tg_user_id"]), page, settings, homoglyph=not plain)
        hint = Text("←/→ страницы   q выход", style="muted")
        return Group(body, Align.center(hint))

    try:
        with Keyboard() as keyboard, Live(render(), console=console, screen=True, refresh_per_second=8) as live:
            while True:
                key = keyboard.read()
                if key in {"q", "Q", "esc"}:
                    break
                if key == "left":
                    page_num = max(1, page_num - 1)
                elif key == "right":
                    page_num += 1
                live.update(render())
                if not keyboard.enabled:
                    break
                time.sleep(0.08)
    except KeyboardInterrupt:
        pass
    store.close()
    return 0


def cmd_catalog(args: argparse.Namespace, settings: Settings) -> int:
    store = _store(settings)
    total = store.count_groups(args.query)
    page = Page.build(total, args.page, args.size)
    rows = store.list_groups(page=page.page, size=page.size, query=args.query)
    if args.json:
        print(json.dumps({"total": total, "page": page.page, "rows": rows}, ensure_ascii=False, indent=2, default=str))
        store.close()
        return 0
    if not rows:
        console.print(Panel(Text(to_plain(EMPTY_DB), style="#f8f8f2"), border_style="#f1fa8c", box=box.ROUNDED))
        store.close()
        return 0
    table = Table(box=box.ROUNDED, border_style="#44475a", header_style="accent", expand=True)
    table.add_column("#", justify="right", style="muted", width=4)
    table.add_column("чат", ratio=3)
    table.add_column("username", ratio=2)
    table.add_column("участники", justify="right")
    table.add_column("сообщений", justify="right")
    table.add_column("последнее", justify="right")
    for idx, row in enumerate(rows, start=page.offset + 1):
        table.add_row(
            str(idx),
            truncate(row.get("title") or "—", 34),
            f"@{row['username']}" if row.get("username") else "🔒",
            grouped_int(row.get("members_count")) if row.get("members_count") else "—",
            grouped_int(row.get("messages_total")) if row.get("messages_total") else "—",
            fmt_dt(row.get("last_message_at"), settings.tz),
        )
    console.print(table)
    console.print(f"[muted]всего {grouped_int(total)} · страница {page.page} из {page.pages}[/]")
    store.close()
    return 0


def cmd_stats(settings: Settings) -> int:
    store = _store(settings)
    stats = store.stats()
    table = Table.grid(padding=(0, 2))
    table.add_column(style="muted")
    table.add_column(style="title")
    rows = [
        ("групп всего", grouped_int(stats.get("groups"))),
        ("публичных", grouped_int(stats.get("public_groups"))),
        ("с @username", grouped_int(stats.get("with_username"))),
        ("покрытие сессий", f"{grouped_int(stats.get('pairs'))} пар / {grouped_int(stats.get('sessions'))} сессий"),
        ("целей", grouped_int(stats.get("targets"))),
        ("связей цель×чат", grouped_int(stats.get("target_groups"))),
        ("сканов", grouped_int(stats.get("scans"))),
        ("размер базы", human_size(stats.get("db_bytes") or 0)),
        ("обновлено", fmt_relative(stats.get("updated_at"), settings.tz)),
    ]
    for label, value in rows:
        table.add_row(label, value)
    console.print(
        Panel(
            table,
            title="[accent]📚 healdragg[/accent]",
            title_align="left",
            box=box.ROUNDED,
            border_style="#bd93f9",
            padding=(1, 2),
        )
    )
    counts = store.table_counts()
    t2 = Table(box=box.ROUNDED, border_style="#44475a", header_style="accent")
    t2.add_column("таблица")
    t2.add_column("строк", justify="right")
    for name, count in counts.items():
        t2.add_row(name, grouped_int(count))
    console.print(t2)
    store.close()
    return 0


def cmd_doctor(settings: Settings) -> int:
    checks: list[tuple[bool, str, str]] = []
    checks.append((settings.has_api_credentials, "API_ID / API_HASH", "нет — healdragg init"))
    checks.append((bool(settings.bot_token), "BOT_TOKEN", "нет — получи у @BotFather"))
    checks.append((bool(settings.api_token), "API_TOKEN", "нет — сгенерируется в healdragg api"))
    try:
        store = Store(settings.db_path)
        integrity = store.integrity_check()
        checks.append((integrity == "ok", "SQLite", integrity))
        sessions = store.session_names(only_active=False)
        checks.append((bool(sessions), "сессии", ", ".join(sessions) or "нет — session add main"))
        stats = store.stats()
        checks.append((True, "база", f"{stats['groups']} групп · {human_size(stats['db_bytes'])}"))
        store.close()
    except Exception as exc:  # noqa: BLE001
        checks.append((False, "SQLite", str(exc)))
    try:
        import aiogram  # noqa: F401
        import telethon  # noqa: F401

        checks.append((True, "зависимости", "telethon + aiogram на месте"))
    except ImportError as exc:
        checks.append((False, "зависимости", str(exc)))
    checks.append((sys.version_info >= (3, 10), "Python", sys.version.split()[0]))
    console.print("[accent]Проверка окружения[/]")
    ok_all = True
    for ok, name, detail in checks:
        ok_all &= ok
        console.print(f" {'[good]✓[/]' if ok else '[bad]✗[/]'} {name:<18} [muted]{detail}[/]")
    return 0 if ok_all else 1


def cmd_db(args: argparse.Namespace, settings: Settings) -> int:
    store = _store(settings)
    action = args.db_cmd or "stats"
    if action == "optimize":
        before = store.size_bytes()
        store.optimize()
        after = store.size_bytes()
        console.print(f"[good]✓[/] optimize: {human_size(before)} → {human_size(after)}")
    elif action == "vacuum":
        before = store.size_bytes()
        store.vacuum()
        store.optimize()
        after = store.size_bytes()
        console.print(f"[good]✓[/] vacuum: {human_size(before)} → {human_size(after)}")
    elif action == "check":
        console.print(f"integrity: [accent]{store.integrity_check()}[/]")
    elif action == "stats":
        for name, count in store.table_counts().items():
            console.print(f"{name:<16} {grouped_int(count):>8}")
        console.print(f"{'файл':<16} {human_size(store.size_bytes()):>8}")
    elif action == "export":
        target = store.find_target(args.query)
        if not target:
            console.print("[bad]цель не найдена[/]")
            store.close()
            return 1
        payload = store.export_target(int(target["id"]))
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        if args.out == "-":
            print(text)
        else:
            Path(args.out).write_text(text, encoding="utf-8")
            console.print(f"[good]✓[/] сохранено: {args.out} ({len(payload['groups'])} чатов)")
    elif action == "import":
        payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
        pk = store.import_target(payload)
        console.print(f"[good]✓[/] импортировано, target_pk={pk}")
    store.close()
    return 0


def cmd_bot(args: argparse.Namespace, settings: Settings) -> int:
    from .bot.app import run_bot

    if args.token:
        settings.bot_token = args.token
    settings.require_bot()
    try:
        asyncio.run(run_bot(settings))
    except KeyboardInterrupt:
        pass
    return 0


def cmd_api(args: argparse.Namespace, settings: Settings) -> int:
    try:
        import uvicorn
    except ImportError:
        console.print("[bad]нужен uvicorn[/] · pip install uvicorn")
        return 1
    from .api import create_app

    host = args.host or settings.api_host
    port = args.port or settings.api_port
    token = settings.ensure_api_token()
    if args.show_token:
        console.print(f"[accent]токен:[/] {token}")
    console.print(
        Panel(
            Text.from_markup(
                f"🌐 [accent]http://{host}:{port}[/]\n"
                f"🔑 токен: [muted]{token}[/]\n\n"
                "В Android-приложении укажи адрес и токен.\n"
                "Для доступа с телефона по локальной сети используй IP компьютера."
            ),
            box=box.ROUNDED,
            border_style="#bd93f9",
        )
    )
    app = create_app(settings)
    uvicorn.run(app, host=host, port=port, log_level=settings.log_level.lower())
    return 0


def cmd_tui(args: argparse.Namespace, settings: Settings) -> int:
    from .tui import run_tui

    try:
        asyncio.run(run_tui(settings, once=args.once))
    except KeyboardInterrupt:
        pass
    return 0


# ── диспетчер ──────────────────────────────────────────────────────────────
def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = _settings(args)

    handlers: dict[str, Any] = {
        "init": lambda: cmd_init(settings),
        "demo": lambda: cmd_demo(args, settings),
        "session": lambda: cmd_session(args, settings),
        "bootstrap": lambda: cmd_bootstrap(args, settings),
        "target": lambda: cmd_target(args, settings),
        "scan": lambda: cmd_scan(args, settings),
        "cover": lambda: cmd_cover(args, settings),
        "groups": lambda: cmd_groups(args, settings),
        "catalog": lambda: cmd_catalog(args, settings),
        "stats": lambda: cmd_stats(settings),
        "doctor": lambda: cmd_doctor(settings),
        "db": lambda: cmd_db(args, settings),
        "bot": lambda: cmd_bot(args, settings),
        "api": lambda: cmd_api(args, settings),
        "tui": lambda: cmd_tui(args, settings),
    }

    if not args.command:
        _print_banner()
        console.print("[accent]команды:[/]")
        for line in (
            "  [bold]init[/]        настроить .env и базу",
            "  [bold]demo[/]        посмотреть, как всё выглядит",
            "  [bold]session[/]     аккаунты-сканеры",
            "  [bold]bootstrap[/]   собрать каталог публичных чатов",
            "  [bold]target[/]      цели",
            "  [bold]scan[/]        проверить участие цели",
            "  [bold]cover[/]       собрать базу + проверить цель одной командой",
            "  [bold]groups[/]      список чатов (с листалкой)",
            "  [bold]catalog[/]     каталог групп",
            "  [bold]stats[/]       статистика",
            "  [bold]bot[/]         @healdragg_bot",
            "  [bold]api[/]         панель для Android",
            "  [bold]tui[/]         живая панель",
            "  [bold]doctor[/]      проверка окружения",
        ):
            console.print(line)
        console.print("\n[muted]подробнее:[/] healdragg <команда> --help")
        return 0

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 2

    try:
        return int(handler() or 0)
    except SystemExit as exc:
        return int(exc.code or 0)
    except KeyboardInterrupt:
        err.print("\n[warn]прервано[/]")
        return 130
    except Exception as exc:  # noqa: BLE001
        if args.debug:
            raise
        err.print(f"[bad]ошибка:[/] {exc}")
        err.print("[muted]подробности: healdragg --debug <команда>[/]")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
