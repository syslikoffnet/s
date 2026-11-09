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
