"""Поиск коллекций не теряет курсор, лимит и отменённые пользователем задачи."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient
from telethon.errors import RPCError
from telethon.tl import types
from telethon.tl.types.payments import UniqueStarGift

from healdragg.api import create_app
from healdragg.config import Settings
from healdragg.db import Store
from healdragg.gifts import crawl_nft_chain
from healdragg.tg import RateLimiter

HEADERS = {"X-Api-Token": "secret"}


def unique(num: int) -> types.StarGiftUnique:
    return types.StarGiftUnique(id=num, gift_id=9, title="Мишка", slug=f"Bear-{num}", num=num,
                                attributes=[], availability_issued=3, availability_total=3,
                                owner_id=types.PeerUser(user_id=100 + num))


def pool_for(client):
    class Pool:
        async def first(self, sessions=None):
            return SimpleNamespace(client=client, limiter=RateLimiter(0, 0))
    return Pool()


def settings_for(tmp_path):
    return Settings(home=tmp_path, db_path=tmp_path / "api.db", sessions_dir=tmp_path / "sessions",
                    api_token="secret", auto_backup=False)


async def test_wrapped_response_and_network_error_never_skip_numbers(tmp_path):
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    store.upsert_nft_collection("Bear", total=3)
    run = store.create_nft_chain_run(10, False, None, None)
    requests = []

    class Client:
        async def __call__(self, request):
            requests.append(request.slug)
            if request.slug == "Bear-1":
                return UniqueStarGift(gift=unique(1), chats=[], users=[])
            if request.slug == "Bear-2":
                raise RPCError(request, "STARGIFT_SLUG_INVALID", 400)
            raise RuntimeError("нет сети")

    with pytest.raises(RuntimeError, match="нет сети"):
        await crawl_nft_chain(pool_for(Client()), store, settings, requests=10, discover=False, run_id=run)
    progress = store.nft_chain_run(run)
    collection = store.next_nft_collection()
    assert requests == ["Bear-1", "Bear-2", "Bear-3"]
    assert progress["used"] == 3 and progress["inflight"] == "Bear-3"
    assert collection["cursor"] == 3 and collection["scanned"] == 2 and collection["errors"] == 1
    assert store.nft_items()[0]["slug"] == "Bear-1"
    assert store.nft_items()[0]["owner_tg_id"] == 101
    store.close()


async def test_interrupted_request_is_counted_and_cursor_is_retried(tmp_path):
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    store.upsert_nft_collection("Bear", total=3)
    run = store.create_nft_chain_run(3, False, None, None)
    started = asyncio.Event()
    calls = []

    class Client:
        async def __call__(self, request):
            calls.append(request.slug)
            if request.slug == "Bear-2":
                started.set()
                await asyncio.Event().wait()
            return UniqueStarGift(gift=unique(1), chats=[], users=[])

    task = asyncio.create_task(crawl_nft_chain(pool_for(Client()), store, settings,
                                                requests=3, discover=False, run_id=run))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.nft_chain_run(run)["used"] == 2
    assert store.next_nft_collection()["cursor"] == 2

    class Resumed:
        async def __call__(self, request):
            calls.append(request.slug)
            return UniqueStarGift(gift=unique(2), chats=[], users=[])

    await crawl_nft_chain(pool_for(Resumed()), store, settings, requests=3, discover=False, run_id=run)
    assert calls == ["Bear-1", "Bear-2", "Bear-2"]
    assert store.nft_chain_run(run)["used"] == 3
    assert store.next_nft_collection()["cursor"] == 3
    assert store.nft_collections()[0]["scanned"] == 2
    store.close()


async def test_discovery_resumes_without_replaying_completed_catalog_entries(tmp_path):
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    run = store.create_nft_chain_run(20, True, None, None)
    calls = []
    fail = True

    class Client:
        async def __call__(self, request):
            nonlocal fail
            if type(request).__name__ == "GetStarGiftsRequest":
                calls.append("catalog")
                return SimpleNamespace(gifts=[SimpleNamespace(id=1, availability_total=10),
                                              SimpleNamespace(id=2, availability_total=10)])
            calls.append(f"resale:{request.gift_id}")
            if request.gift_id == 2 and fail:
                fail = False
                raise RuntimeError("нет сети")
            return SimpleNamespace(gifts=[])

    with pytest.raises(RuntimeError, match="нет сети"):
        await crawl_nft_chain(pool_for(Client()), store, settings, requests=20, discover=True, run_id=run)
    assert store.nft_chain_run(run)["discovery_index"] == 1
    await crawl_nft_chain(pool_for(Client()), store, settings, requests=20, discover=True, run_id=run)
    assert calls == ["catalog", "resale:1", "resale:2", "resale:2"]
    assert store.nft_chain_run(run)["discovery_done"] == 1
    assert store.nft_chain_run(run)["used"] == 4
    store.close()


def test_api_restarts_active_search_but_not_user_stopped_search(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    entered = threading.Event()
    calls = []

    async def crawl(*args, **kwargs):
        calls.append(kwargs)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("healdragg.api.crawl_nft_chain", crawl)
    app = create_app(settings)
    app.state.hd.store.add_session("chosen", "chosen.session")
    with TestClient(app) as client:
        assert client.post("/api/gifts/chain", headers=HEADERS,
                           json={"requests": 10, "discover": False}).status_code == 200
        assert entered.wait(2)
        assert calls[-1]["requests"] == 10
    entered.clear()
    with TestClient(create_app(settings)) as client:
        assert entered.wait(2)
        assert client.post("/api/jobs/gifts%3Achain/cancel", headers=HEADERS).json()["ok"]
        assert client.get("/api/gifts/chain", headers=HEADERS).json()["task"]["status"] == "paused"
    entered.clear()
    with TestClient(create_app(settings)) as client:
        assert not entered.is_set()
        assert client.post("/api/gifts/chain/resume", headers=HEADERS).status_code == 200
        assert entered.wait(2)
        assert client.post("/api/jobs/gifts%3Achain/cancel", headers=HEADERS).json()["ok"]


def test_last_interrupted_reservation_requires_new_budget(tmp_path):
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    store.add_session("chosen", "chosen.session")
    run = store.create_nft_chain_run(1, False, "Bear", 3)
    store.upsert_nft_collection("Bear", total=3)
    assert store.reserve_nft_chain_request(run, "Bear-1")
    store.close()
    with TestClient(create_app(settings)) as client:
        status = client.get("/api/gifts/chain", headers=HEADERS).json()["task"]
        assert status["status"] == "paused" and status["inflight"] == "Bear-1"
        assert client.post("/api/gifts/chain/resume", headers=HEADERS).status_code == 409
        assert client.app.state.hd.store.next_nft_collection()["cursor"] == 1


async def test_discovery_retries_pending_request_even_after_discovery_quota(tmp_path):
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    run = store.create_nft_chain_run(8, True, None, None)
    calls = []

    class Client:
        async def __call__(self, request):
            if type(request).__name__ == "GetStarGiftsRequest":
                calls.append("catalog")
                return SimpleNamespace(gifts=[SimpleNamespace(id=1, availability_total=10)])
            calls.append("resale")
            if calls.count("resale") == 1:
                raise RuntimeError("нет сети")
            return SimpleNamespace(gifts=[])

    with pytest.raises(RuntimeError):
        await crawl_nft_chain(pool_for(Client()), store, settings, requests=8, discover=True, run_id=run)
    assert store.nft_chain_run(run)["discovery_used"] == 2
    await crawl_nft_chain(pool_for(Client()), store, settings, requests=8, discover=True, run_id=run)
    assert calls == ["catalog", "resale", "resale"]
    assert store.nft_chain_run(run)["discovery_index"] == 1
    store.close()


async def test_account_identity_change_stops_search(tmp_path):
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    store.add_session("chosen", "chosen.session", tg_user_id=42)
    run = store.create_nft_chain_run(10, False, None, None, "chosen", 42)

    class Pool:
        async def first(self, names=None):
            assert names == ["chosen"]
            return SimpleNamespace(user_id=43)

    with pytest.raises(RuntimeError, match="исходный аккаунт"):
        await crawl_nft_chain(Pool(), store, settings, requests=10, discover=False, run_id=run)
    assert store.nft_chain_run(run)["used"] == 0
    store.close()


async def test_retry_prioritizes_interrupted_item_over_other_collections(tmp_path):
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    store.upsert_nft_collection("Bear", total=3)
    store.upsert_nft_collection("Other", total=3)
    run = store.create_nft_chain_run(2, False, None, None)
    assert store.reserve_nft_chain_request(run, "Bear-1")
    calls = []

    class Client:
        async def __call__(self, request):
            calls.append(request.slug)
            return UniqueStarGift(gift=unique(1), chats=[], users=[])

    await crawl_nft_chain(pool_for(Client()), store, settings, requests=2, discover=False, run_id=run)
    assert calls == ["Bear-1"]
    assert store.nft_chain_run(run)["used"] == 2
    store.close()


async def test_network_failure_makes_only_one_application_call(tmp_path):
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    store.upsert_nft_collection("Bear", total=3)
    run = store.create_nft_chain_run(5, False, None, None)
    calls = []

    class Client:
        async def __call__(self, request):
            calls.append(request.slug)
            raise OSError("нет сети")

    with pytest.raises(OSError):
        await crawl_nft_chain(pool_for(Client()), store, settings, requests=5, discover=False, run_id=run)
    assert calls == ["Bear-1"]
    assert store.nft_chain_run(run)["used"] == 1
    assert store.next_nft_collection()["cursor"] == 1
    store.close()
