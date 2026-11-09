"""Парсер CLI и настройки, появившиеся во втором раунде."""

from __future__ import annotations

from healdragg.cli import build_parser
from healdragg.config import Settings


def test_cover_defaults() -> None:
    args = build_parser().parse_args(["cover", "7126917488"])
    assert args.command == "cover"
    assert args.query == "7126917488"
    assert args.no_join is False
    assert args.join_limit is None
    assert args.queries is None
    assert args.page == 1


def test_cover_flags() -> None:
    args = build_parser().parse_args(
        [
            "cover",
            "@durov",
            "--no-join",
            "--join-limit",
            "5",
            "--queries",
            "чат",
            "флуд",
            "--page",
            "2",
        ]
    )
    assert args.no_join is True
    assert args.join_limit == 5
    assert args.queries == ["чат", "флуд"]
    assert args.page == 2


def test_all_commands_registered() -> None:
    parser = build_parser()
    cases = {
        "init": [],
        "demo": [],
        "session": ["list"],
        "bootstrap": [],
        "target": ["list"],
        "scan": ["7126917488"],
        "cover": ["7126917488"],
        "activity": ["7126917488"],
        "graph": ["7126917488"],
        "groups": ["7126917488"],
        "catalog": [],
        "stats": [],
        "doctor": [],
        "db": ["stats"],
        "bot": [],
        "api": [],
        "tui": ["--once"],
    }
    for command, extra in cases.items():
        assert parser.parse_args([command, *extra]).command == command


def test_bootstrap_join_setting(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HEALDRAGG_HOME", str(tmp_path))
    monkeypatch.delenv("JOIN_ON_BOOTSTRAP", raising=False)
    assert Settings.load().bootstrap_join is False

    monkeypatch.setenv("JOIN_ON_BOOTSTRAP", "true")
    assert Settings.load().bootstrap_join is True


def test_cover_runs_bootstrap_and_scan(tmp_path, monkeypatch) -> None:
    import asyncio
    import io

    from rich.console import Console

    from healdragg import cli, scan, tg
    from healdragg.db import Store
    from healdragg.demo import seed_demo

    settings = Settings(home=tmp_path, db_path=tmp_path / "cover.db", sessions_dir=tmp_path / "s")
    store = Store(settings.db_path)
    seed_demo(store, total=40)
    store.close()

    seen: dict[str, object] = {}

    async def fake_bootstrap(pool, store, settings, **kwargs):
        seen.update(kwargs)
        return {"groups": 40, "joined": 3}

    async def fake_scan(pool, store, settings, target, reporter=None):
        return {"checked": 40, "found": 40, "errors": 0}

    class FakePool:
        def __init__(self, *args, **kwargs):
            pass

        async def close(self):
            pass

    monkeypatch.setattr(scan, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(scan, "scan_target", fake_scan)
    monkeypatch.setattr(tg, "SessionPool", FakePool)
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=120, theme=None))

    args = cli.build_parser().parse_args(["cover", "7126917488", "--join-limit", "7"])
    assert asyncio.run(cli._cover(args, settings)) == 0

    text = out.getvalue()
    assert seen["join"] is True
    assert seen["join_limit"] == 7
    assert "собрано чатов: 40" in text
    assert "Всего 40" in text
