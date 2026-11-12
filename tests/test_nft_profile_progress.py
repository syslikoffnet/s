"""Прогресс второго шага виден до завершения первого владельца."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telethon.tl import types

from healdragg import gifts
from healdragg.config import Settings
from healdragg.db import Store
from healdragg.tg import RateLimiter


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "gifts.db")
    store.upsert_nft_item("Bear-1", owner_tg_id=1, ownership_observed=True)
    settings = Settings(home=tmp_path, db_path=tmp_path / "gifts.db", sessions_dir=tmp_path / "sessions")
    yield store, settings
    store.close()


async def test_progress_before_network_and_during_first_owner(setup):
    store, settings = setup
    waiting = asyncio.Event()
    unblock = asyncio.Event()
    events = []

    class Client:
        async def get_entity(self, query):
            return types.User(id=1, access_hash=9, first_name="Владелец")

        async def __call__(self, request):
            if request.offset:
                waiting.set()
                await unblock.wait()
                return SimpleNamespace(gifts=[], users=[], count=1, next_offset=None)
            gift = SimpleNamespace(gift=SimpleNamespace(id=5, title="Rose", stars=10),
                                   saved_id=10, date=datetime(2025, 1, 1, tzinfo=timezone.utc))
            return SimpleNamespace(gifts=[gift], users=[], count=1, next_offset="next")

    class Pool:
        async def first(self, sessions=None):
            return SimpleNamespace(client=Client(), limiter=RateLimiter(0, 0))

    async def report(state):
        events.append(dict(state))

    task = asyncio.create_task(gifts.crawl_nft_profiles(Pool(), store, settings, limit=3, reporter=report))
    await asyncio.wait_for(waiting.wait(), timeout=2)
    assert any("подключаю аккаунт" in e["phase"] for e in events)
    assert any("ищу профиль" in e["phase"] for e in events)
    assert any("запрашиваю подарки" in e["phase"] for e in events)
    live = events[-1]
    assert live["page_received"] == 1 and live["found"] == 1
    assert live["done"] == 0 and live["total"] == 3 and live["owners_done"] == 0
    assert store.list_gifts(1)
    unblock.set()
    result = await asyncio.wait_for(task, timeout=2)
    assert result["profiles"] == 1 and result["owners"] == 1 and result["new"] == 1
    assert events[-1]["done"] == 1 and events[-1]["found"] == 1


async def test_cancel_during_connection_is_not_swallowed(setup):
    store, settings = setup
    waiting = asyncio.Event()
    events = []

    class Pool:
        async def first(self, sessions=None):
            waiting.set()
            await asyncio.Event().wait()

    async def report(state):
        events.append(dict(state))

    task = asyncio.create_task(gifts.crawl_nft_profiles(Pool(), store, settings, reporter=report))
    await asyncio.wait_for(waiting.wait(), 2)
    assert "подключаю аккаунт" in events[-1]["phase"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_errors_and_owner_limit_are_reported(setup, monkeypatch):
    store, settings = setup
    store.upsert_nft_item("Bear-2", owner_tg_id=2, ownership_observed=True)
    events = []

    async def fake_crawl(pool, store, settings, query, **kwargs):
        if query == "1":
            raise RuntimeError("недоступен")
        await kwargs["reporter"]({"phase": "читаю", "profiles_done": 0, "new_gifts": 2})
        return {"profiles": 1, "new": 2, "errors": 1, "results": [{"error": "сбой подарков"}]}

    async def report(state):
        events.append(dict(state))

    monkeypatch.setattr(gifts, "crawl_gifts", fake_crawl)
    result = await gifts.crawl_nft_profiles(None, store, settings, limit=2, reporter=report)
    assert result["owners"] == 2 and result["profiles"] == 1 and result["errors"] == 2
    assert result["last_error"] == "сбой подарков"
    assert events[-1]["errors"] == 2 and events[-1]["owners_done"] == 2
    assert any(e.get("found") == 2 and e["done"] == 0 for e in events)
