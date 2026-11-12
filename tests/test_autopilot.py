from __future__ import annotations

from pathlib import Path

from healdragg import autopilot
from healdragg.config import Settings
from healdragg.db import Store


class FakePool:
    async def close(self) -> None:
        pass


async def test_autopilot_tick_scans_and_collects(store: Store, tmp_path: Path, monkeypatch) -> None:
    settings = Settings(home=tmp_path, db_path=tmp_path / "a.db", sessions_dir=tmp_path / "s")
    settings.auto_activity_on_scan = True
    store.upsert_target(42, username="t")
    store.add_session("main", "/tmp/m.session", tg_user_id=1, status="active")
    calls: list[str] = []

    async def fake_scan(pool, store_, settings_, target, **kwargs):
        calls.append("scan")
        return {"alerts": 2}

    async def fake_activity(pool, store_, settings_, target, **kwargs):
        calls.append("activity")
        return {"messages": 5, "alerts": 1}

    monkeypatch.setattr(autopilot, "scan_target", fake_scan)
    monkeypatch.setattr(autopilot, "collect_activity", fake_activity)

    stats = await autopilot.tick(FakePool(), store, settings)
    assert calls == ["scan", "activity"]
    assert stats["scanned"] == 1
    assert stats["messages"] == 5
    assert stats["alerts"] == 3


async def test_autopilot_without_sessions(store: Store, tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, db_path=tmp_path / "b.db", sessions_dir=tmp_path / "s")
    store.upsert_target(43)
    stats = await autopilot.tick(FakePool(), store, settings)
    assert stats["errors"] == 1
    assert stats["scanned"] == 0


async def test_autopilot_reaches_all_targets_when_updates_change_sort_order(store, tmp_path, monkeypatch):
    settings = Settings(home=tmp_path, db_path=tmp_path / "a.db", sessions_dir=tmp_path / "s")
    store.add_session("main", "main.session")
    for tg_id in range(1, 126):
        store.upsert_target(tg_id)
    seen = []

    async def scan(pool, db, settings, target, **kwargs):
        seen.append(target["tg_user_id"])
        db.update_target_profile(target["tg_user_id"], bio="updated")
        return {}

    monkeypatch.setattr(autopilot, "scan_target", scan)
    stats = await autopilot.tick(FakePool(), store, settings)
    assert stats["scanned"] == 125
    assert sorted(seen) == list(range(1, 126))


async def test_autopilot_resumes_after_interruption(store, tmp_path, monkeypatch):
    import asyncio

    import pytest

    settings = Settings(home=tmp_path, db_path=tmp_path / "a.db", sessions_dir=tmp_path / "s")
    store.add_session("main", "main.session")
    for tg_id in (1, 2, 3):
        store.upsert_target(tg_id)
    seen = []
    interrupted = True

    async def scan(pool, db, settings, target, **kwargs):
        nonlocal interrupted
        tg_id = target["tg_user_id"]
        seen.append(tg_id)
        if tg_id == 2 and interrupted:
            interrupted = False
            raise asyncio.CancelledError
        return {}

    monkeypatch.setattr(autopilot, "scan_target", scan)
    with pytest.raises(asyncio.CancelledError):
        await autopilot.tick(FakePool(), store, settings)
    await autopilot.tick(FakePool(), store, settings)
    assert seen == [1, 2, 2, 3]
    assert store.get_meta("autopilot_cursor") == "0"


async def test_browsed_profile_not_automatically_monitored(store, tmp_path, monkeypatch):
    settings = Settings(home=tmp_path, db_path=tmp_path / "a.db", sessions_dir=tmp_path / "s")
    store.add_session("main", "main.session")
    old = store.upsert_target(1)
    new = store.upsert_target(2, watch_enabled=False)
    # Further updates may not silently turn monitoring on or off.
    assert store.upsert_target(2)["watch_enabled"] == 0
    assert store.upsert_target(1, watch_enabled=False)["watch_enabled"] == 1
    seen = []

    async def scan(pool, db, settings, target, **kwargs):
        seen.append(target["tg_user_id"])
        return {}

    monkeypatch.setattr(autopilot, "scan_target", scan)
    stats = await autopilot.tick(FakePool(), store, settings)
    assert (old["watch_enabled"], new["watch_enabled"]) == (1, 0)
    assert seen == [1] and stats["targets"] == 1
