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


def test_multiple_saved_nft_runs_archive_and_resume_with_extra_budget(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    entered = threading.Event()

    async def hold(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("healdragg.api.crawl_nft_chain", hold)
    app = create_app(settings)
    app.state.hd.store.add_session("chosen", "chosen.session")
    with TestClient(app) as client:
        first = client.post("/api/gifts/chain", headers=HEADERS, json={"requests": 1}).json()["state"]["run_id"]
        assert entered.wait(2)
        assert client.post("/api/jobs/gifts:chain/cancel", headers=HEADERS).json()["ok"]
        store = app.state.hd.store
        store.set_nft_chain_status(first, "running")
        assert store.reserve_nft_chain_request(first, "Bear-1")
        store.set_nft_chain_status(first, "paused")
        assert client.post(f"/api/gifts/chain/runs/{first}/archive", headers=HEADERS,
                           json={"archived": True}).json()["ok"]
        assert client.post(f"/api/gifts/chain/runs/{first}/resume", headers=HEADERS).status_code == 409
        entered.clear()
        second = client.post("/api/gifts/chain", headers=HEADERS, json={"requests": 10}).json()["state"]["run_id"]
        assert entered.wait(2) and second != first
        assert client.post("/api/jobs/gifts:chain/cancel", headers=HEADERS).json()["ok"]
        assert client.post(f"/api/gifts/chain/runs/{first}/archive", headers=HEADERS,
                           json={"archived": False}).json()["ok"]
        assert {r["id"] for r in client.get("/api/gifts/chain/runs", headers=HEADERS).json()["rows"]} == {first, second}
        assert client.post(f"/api/gifts/chain/runs/{first}/resume", headers=HEADERS).status_code == 409
        entered.clear()
        res = client.post(f"/api/gifts/chain/runs/{first}/resume", headers=HEADERS, json={"extra_requests": 5})
        assert res.status_code == 200 and entered.wait(2)
        assert store.nft_chain_run(first)["budget"] == 6
        assert client.post(f"/api/gifts/chain/runs/{first}/archive", headers=HEADERS,
                           json={"archived": True}).status_code == 409
        assert client.post("/api/jobs/gifts:chain/cancel", headers=HEADERS).json()["ok"]


async def test_resume_old_inflight_after_another_search_advanced_collection(tmp_path):
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    store.upsert_nft_collection("Bear", total=3)
    run = store.create_nft_chain_run(5, False, None, None)
    assert store.reserve_nft_chain_request(run, "Bear-1")
    store.advance_nft_collection("Bear", cursor=2, scanned=1, owners=1, on_chain=0)
    calls = []

    class Client:
        async def __call__(self, request):
            calls.append(request.slug)
            return UniqueStarGift(gift=unique(int(request.slug.split("-")[1])), chats=[], users=[])

    await crawl_nft_chain(pool_for(Client()), store, settings, requests=5, discover=False, run_id=run)
    assert calls == ["Bear-2", "Bear-3"]
    assert store.nft_chain_run(run)["inflight"] is None
    store.close()


async def test_discovery_saves_resale_nft_and_reports_progress(tmp_path):
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    run = store.create_nft_chain_run(8, True, None, None)
    progress = []

    class Client:
        async def __call__(self, request):
            if type(request).__name__ == "GetStarGiftsRequest":
                return SimpleNamespace(gifts=[SimpleNamespace(id=9, availability_total=3)])
            if type(request).__name__ == "GetResaleStarGiftsRequest":
                return SimpleNamespace(gifts=[unique(2)], users=[types.User(id=102, username="seller")])
            return UniqueStarGift(gift=unique(int(request.slug.rsplit("-", 1)[1])), chats=[],
                                  users=[types.User(id=102, username="seller")])

    async def report(data):
        progress.append(dict(data))

    result = await crawl_nft_chain(pool_for(Client()), store, settings, requests=8, reporter=report, run_id=run)
    assert result["new"] == 3
    assert store.nft_chain_run(run)["new_items"] == 3
    assert {row["slug"] for row in store.nft_items()} == {"Bear-1", "Bear-2", "Bear-3"}
    assert store.query_one("SELECT owner_username FROM nft_items WHERE slug='Bear-2'")["owner_username"] == "seller"
    assert any("каталог" in event["phase"] for event in progress)
    assert any("продажи" in event["phase"] and event["found"] == 1 for event in progress)
    assert any("Bear-1" in event["phase"] for event in progress)
    store.close()


async def test_discovery_count_survives_restart_after_insert(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    run = store.create_nft_chain_run(8, True, None, None)
    advance = store.advance_nft_chain_discovery
    fail = True

    def interrupt(run_id, index):
        nonlocal fail
        if fail:
            fail = False
            raise OSError("ошибка записи позиции")
        advance(run_id, index)

    monkeypatch.setattr(store, "advance_nft_chain_discovery", interrupt)

    class Client:
        async def __call__(self, request):
            if type(request).__name__ == "GetStarGiftsRequest":
                return SimpleNamespace(gifts=[SimpleNamespace(id=9, availability_total=3)])
            if type(request).__name__ == "GetResaleStarGiftsRequest":
                return SimpleNamespace(gifts=[unique(2)], users=[])
            return UniqueStarGift(gift=unique(int(request.slug.rsplit("-", 1)[1])), chats=[], users=[])

    with pytest.raises(OSError, match="позиции"):
        await crawl_nft_chain(pool_for(Client()), store, settings, requests=8, run_id=run)
    assert store.nft_chain_run(run)["new_items"] == 1
    assert store.nft_chain_run(run)["inflight"] == "discovery:resale:9"
    await crawl_nft_chain(pool_for(Client()), store, settings, requests=8, run_id=run)
    assert store.nft_chain_run(run)["new_items"] == 3
    assert len(store.nft_items()) == 3
    store.close()


async def test_wrong_nft_response_never_advances_or_saves(tmp_path):
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    store.upsert_nft_collection("Bear", total=3)
    run = store.create_nft_chain_run(3, False, None, None)

    class Client:
        async def __call__(self, request):
            return UniqueStarGift(gift=unique(2), chats=[], users=[])

    with pytest.raises(ValueError, match="другой NFT"):
        await crawl_nft_chain(pool_for(Client()), store, settings, requests=3, discover=False, run_id=run)
    assert store.nft_items() == []
    assert store.next_nft_collection()["cursor"] == 1
    assert store.nft_chain_run(run)["inflight"] == "Bear-1"
    store.close()


def test_empty_collection_discovery_explains_result(tmp_path, monkeypatch):
    import time

    settings = settings_for(tmp_path)

    async def crawl(*args, **kwargs):
        return {"collections_known": 0, "items": 0}

    monkeypatch.setattr("healdragg.api.crawl_nft_chain", crawl)
    app = create_app(settings)
    app.state.hd.store.add_session("chosen", "chosen.session")
    with TestClient(app) as client:
        run = client.post("/api/gifts/chain", headers=HEADERS, json={"requests": 10}).json()["state"]["run_id"]
        for _ in range(100):
            state = client.get("/api/gifts/chain", headers=HEADERS).json()["task"]
            if state["status"] == "done":
                break
            time.sleep(.01)
        assert state["id"] == run and state["status"] == "done"
        assert "Коллекции не найдены" in state["last_error"]


async def test_discovery_rejects_resale_from_another_collection(tmp_path):
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    run = store.create_nft_chain_run(8, True, None, None)

    class Client:
        async def __call__(self, request):
            if type(request).__name__ == "GetStarGiftsRequest":
                return SimpleNamespace(gifts=[SimpleNamespace(id=8, availability_total=3)])
            return SimpleNamespace(gifts=[unique(1)], users=[])

    with pytest.raises(ValueError, match="другой коллекции"):
        await crawl_nft_chain(pool_for(Client()), store, settings, requests=8, run_id=run)
    assert store.nft_items() == []
    assert store.nft_chain_run(run)["discovery_index"] == 0
    assert store.nft_chain_run(run)["inflight"] == "discovery:resale:8"
    store.close()


async def test_discovery_skips_only_explicit_invalid_gift(tmp_path):
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    run = store.create_nft_chain_run(12, True, None, None)
    calls = []

    class Client:
        async def __call__(self, request):
            if type(request).__name__ == "GetStarGiftsRequest":
                return SimpleNamespace(gifts=[SimpleNamespace(id=8, availability_total=3),
                                              SimpleNamespace(id=9, availability_total=3)])
            if type(request).__name__ == "GetResaleStarGiftsRequest":
                calls.append(request.gift_id)
                if request.gift_id == 8:
                    raise RPCError(request, "STARGIFT_INVALID", 400)
                return SimpleNamespace(gifts=[unique(2)], users=[])
            return UniqueStarGift(gift=unique(int(request.slug.rsplit("-", 1)[1])), chats=[], users=[])

    await crawl_nft_chain(pool_for(Client()), store, settings, requests=12, run_id=run)
    assert calls == [8, 9]
    assert store.nft_chain_run(run)["errors"] == 1
    assert store.nft_chain_run(run)["new_items"] == 3
    assert store.nft_chain_run(run)["discovery_index"] == 2
    store.close()
