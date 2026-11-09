from __future__ import annotations

from pathlib import Path

import pytest

from healdragg import scan as scan_mod
from healdragg.config import Settings
from healdragg.db import Store
from healdragg.scan import Reporter, bootstrap, scan_target


class FakePool:
    async def connect(self, name: str):
        raise AssertionError("сеть в этом тесте не нужна")

    async def close(self) -> None:
        pass


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(home=tmp_path, db_path=tmp_path / "s.db", sessions_dir=tmp_path / "sessions")


async def test_bootstrap_accepts_plain_callback(store: Store, settings: Settings, monkeypatch) -> None:
    store.add_session("main", "/tmp/x.session", tg_user_id=1, status="active")

    async def fake_sync(*args, **kwargs):
        return {"checked": 3, "groups": 2, "private": 0, "errors": 0}

    async def fake_search(*args, **kwargs):
        return {"checked": 1, "groups": 1, "public": 1, "errors": 0}

    monkeypatch.setattr(scan_mod, "sync_dialogs", fake_sync)
    monkeypatch.setattr(scan_mod, "search_public", fake_search)
    seen: list[dict] = []

    async def callback(state):
        seen.append(state)

    result = await bootstrap(store=store, pool=FakePool(), settings=settings, reporter=callback)
    assert result["groups"] == 3
    assert seen, "прогресс должен доходить до колбэка"
    scan_row = store.last_scan()
    assert scan_row["status"] == "done"
    # повторный bootstrap не падает на «reporter уже Reporter»
    result2 = await bootstrap(store=store, pool=FakePool(), settings=settings)
    assert result2["groups"] == 3


async def test_scan_target_prefers_own_session(store: Store, settings: Settings, monkeypatch) -> None:
    target = store.upsert_target(42, username="t")
    store.add_session("own", "/tmp/o.session", tg_user_id=42, status="active")
    calls: list[str] = []

    async def fake_own(pool, store_, target_, name, reporter=None):
        calls.append(name)
        gid = store_.upsert_group(500, title="чат", username="c", last_message_at=1)
        store_.upsert_target_group(int(target_["id"]), gid, status="member", source="own_session")
        return {"checked": 1, "found": 1, "meta": 1, "errors": 0}

    monkeypatch.setattr(scan_mod, "scan_own_dialogs", fake_own)
    result = await scan_target(FakePool(), store, settings, target)
    assert calls == ["own"]
    assert result["mode"] == "own_session"
    assert store.count_target_groups(int(target["id"])) == 1
    scan_row = store.last_scan(int(target["id"]))
    assert scan_row["status"] == "done"


async def test_scan_target_without_coverage_is_graceful(store: Store, settings: Settings) -> None:
    target = store.upsert_target(43)
    store.add_session("main", "/tmp/m.session", tg_user_id=1, status="active")
    result = await scan_target(FakePool(), store, settings, target, use_global_search=False)
    assert result["mode"] == "probe"
    assert result["found"] == 0


async def test_reporter_writes_progress(store: Store) -> None:
    scan_id = store.start_scan("probe", total=10)
    updates: list[dict] = []
    reporter = Reporter(store, scan_id=scan_id, callback=updates.append, min_interval=0)
    await reporter.update(done=5, total=10, found=2, phase="проверка", force=True)
    row = store.get_scan(scan_id)
    assert row["done"] == 5
    assert row["found"] == 2
    assert updates[-1]["phase"] == "проверка"
    await reporter.event("что-то пошло не так", "error")
    assert reporter.errors == 1
    assert store.recent_events(1)[0]["level"] == "error"
