"""Наблюдения профилей не стирают историю и не выдумывают продажи."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.tl import types

from healdragg.config import Settings
from healdragg.db import Store
from healdragg.gifts import crawl_gifts
from healdragg.tg import RateLimiter


class Client:
    def __init__(self):
        self.pages = {}
        self.calls = []

    async def get_entity(self, query):
        return types.User(id=int(query), access_hash=9, first_name=f"Человек {query}")

    async def __call__(self, request):
        owner = request.peer.user_id
        self.calls.append((owner, request.offset))
        page = self.pages[(owner, request.offset)]
        if isinstance(page, Exception):
            raise page
        return page


def page(gifts=(), *, count=None, next_offset=None):
    return SimpleNamespace(gifts=list(gifts), users=[], count=count, next_offset=next_offset)


def gift(saved_id: int, *, slug: str | None = None):
    if slug:
        item = types.StarGiftUnique(id=77, gift_id=44, title="Мишка", slug=slug, num=1,
                                    attributes=[], availability_issued=1, availability_total=100)
    else:
        item = SimpleNamespace(id=44, title="Мишка", stars=10)
    return SimpleNamespace(gift=item, saved_id=saved_id, date=datetime(2025, 1, 1, tzinfo=timezone.utc))


@pytest.fixture()
def setup(tmp_path: Path):
    store = Store(tmp_path / "gifts.db")
    client = Client()
    account = SimpleNamespace(client=client, limiter=RateLimiter(0, 0))

    class Pool:
        async def first(self, sessions=None):
            return account

    settings = Settings(home=tmp_path, db_path=tmp_path / "gifts.db", sessions_dir=tmp_path / "sessions")
    yield store, client, Pool(), settings
    store.close()


async def scan(setup, owner: int, per_profile: int = 100):
    store, _, pool, settings = setup
    return await crawl_gifts(pool, store, settings, str(owner), per_profile=per_profile)


async def test_missing_and_transfer_are_observations(setup):
    store, client, _, _ = setup
    client.pages[(1, "")] = page([gift(1), gift(2, slug="Bear-1")])
    await scan(setup, 1)
    client.pages[(1, "")] = page([])
    await scan(setup, 1)
    rows = store.list_gifts(1)
    assert {r["presence"] for r in rows} == {"hidden", "hidden_or_sold"}
    client.pages[(2, "")] = page([gift(3, slug="Bear-1")])
    await scan(setup, 2)
    assert next(r for r in store.list_gifts(1) if r["is_nft"])["presence"] == "previous_owner"
    assert store.list_gifts(2)[0]["presence"] == "visible"
    assert [r["owner_tg_id"] for r in store.nft_history("Bear-1")] == [1, 2]
    await scan(setup, 2)
    assert len(store.nft_history("Bear-1")) == 2


async def test_truncated_or_failed_scan_never_marks_absence(setup):
    store, client, _, _ = setup
    client.pages[(1, "")] = page([gift(1), gift(2)])
    await scan(setup, 1)
    client.pages[(1, "")] = page([gift(i) for i in range(1, 11)], next_offset="next")
    result = await scan(setup, 1, per_profile=10)
    assert not result["results"][0]["complete"]
    assert all(r["presence"] == "visible" for r in store.list_gifts(1))
    client.pages[(1, "")] = page([gift(1)], next_offset="next")
    client.pages[(1, "next")] = RuntimeError("ошибка сети")
    await scan(setup, 1)
    assert all(r["presence"] == "visible" for r in store.list_gifts(1))


async def test_heavy_profile_is_deferred_before_parsing(setup):
    store, client, _, _ = setup
    client.pages[(1, "")] = page([gift(1)], count=1000, next_offset="next")
    result = await scan(setup, 1, per_profile=5000)
    assert result["results"][0]["deferred"]
    assert client.calls == [(1, "")]
    assert store.list_gifts(1) == []
    assert store.heavy_gift_profiles()[0]["gift_count"] == 1000


async def test_reappearance_and_incomplete_page_count(setup):
    store, client, _, _ = setup
    client.pages[(1, "")] = page([gift(1), gift(2, slug="Bear-1")])
    await scan(setup, 1)
    client.pages[(1, "")] = page([gift(1)], count=2)
    result = await scan(setup, 1)
    assert not result["results"][0]["complete"]
    assert next(r for r in store.list_gifts(1) if r["is_nft"])["presence"] == "visible"
    client.pages[(1, "")] = page([])
    await scan(setup, 1)
    client.pages[(1, "")] = page([gift(2, slug="Bear-1")])
    await scan(setup, 1)
    assert next(r for r in store.list_gifts(1) if r["is_nft"])["presence"] == "visible"


async def test_owners_include_regular_and_deferred_profiles(setup):
    store, client, _, _ = setup
    client.pages[(1, "")] = page([gift(1)])
    client.pages[(2, "")] = page([gift(2)], count=1000)
    await scan(setup, 1)
    await scan(setup, 2)
    owners = {row["owner_tg_id"]: row for row in store.all_gift_owners()}
    assert owners[1]["gifts"] == 1 and owners[1]["nft"] == 0
    assert owners[2]["deferred_count"] == 1000
    assert [row["owner_tg_id"] for row in store.all_gift_owners(q="Человек 2")] == [2]


async def test_inventory_does_not_present_hidden_as_confirmed(setup):
    store, client, _, _ = setup
    client.pages[(1, "")] = page([gift(1, slug="Bear-1")])
    await scan(setup, 1)
    assert store.nft_items()[0]["owner_presence"] == "visible"
    client.pages[(1, "")] = page([])
    await scan(setup, 1)
    assert store.nft_items()[0]["owner_presence"] == "hidden"


async def test_heavy_scan_streams_and_failed_pass_preserves_absences(setup):
    import asyncio

    store, client, pool, settings = setup
    store.add_gift(1, saved_id=99, gift_id=44, title="Ранее видимый")
    store.defer_gift_profile(1, None, "Человек 1", 1001)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def request(req):
        if not req.offset:
            return page([gift(1)], count=1001, next_offset="next")
        entered.set()
        await release.wait()
        raise RuntimeError("сеть недоступна")

    client.__class__ = type("SlowClient", (Client,), {"__call__": lambda self, req: request(req)})
    task = asyncio.create_task(crawl_gifts(
        pool, store, settings, "1", allow_heavy=True, per_profile=100_000,
    ))
    await asyncio.wait_for(entered.wait(), 2)
    assert len(store.list_gifts(1)) == 2  # первая страница уже записана
    release.set()
    result = await task
    assert result["errors"] == 1
    assert all(row["presence"] == "visible" for row in store.list_gifts(1))
    assert store.heavy_gift_profiles()[0]["owner_tg_id"] == 1


async def test_known_profile_is_skipped_but_cached_sender_is_crawled(setup):
    store, client, pool, settings = setup
    store.add_gift(1, saved_id=1, gift_id=44, title="Мишка", from_tg_id=2)
    existing_id = store.list_gifts(1)[0]["id"]
    store.finish_gift_snapshot(1, {existing_id})
    client.pages[(2, "")] = page([gift(2)])
    result = await crawl_gifts(pool, store, settings, "1", depth=2, max_profiles=2, skip_known=True)
    assert client.calls == [(2, "")]
    assert result["results"][0]["skipped_known"]
    assert len(store.list_gifts(2)) == 1


async def test_heavy_repeated_offset_is_incomplete(setup):
    store, client, pool, settings = setup
    store.add_gift(1, saved_id=99, gift_id=44, title="Ранее видимый")
    client.pages[(1, "")] = page([gift(1)], count=1000, next_offset="next")
    client.pages[(1, "next")] = page([gift(2)], count=1000, next_offset="next")
    result = await crawl_gifts(pool, store, settings, "1", allow_heavy=True, per_profile=5000)
    assert not result["results"][0]["complete"]
    assert all(row["presence"] == "visible" for row in store.list_gifts(1))


async def test_regular_profile_streams_when_count_is_known(setup):
    import asyncio

    store, client, pool, settings = setup
    entered = asyncio.Event()
    release = asyncio.Event()

    async def request(req):
        if not req.offset:
            return page([gift(1)], count=2, next_offset="next")
        entered.set()
        await release.wait()
        return page([gift(2)], count=2)

    client.__class__ = type("StreamingClient", (Client,), {"__call__": lambda self, req: request(req)})
    task = asyncio.create_task(crawl_gifts(pool, store, settings, "1"))
    await asyncio.wait_for(entered.wait(), 2)
    assert len(store.list_gifts(1)) == 1
    release.set()
    result = await task
    assert result["results"][0]["complete"]
    assert len(store.list_gifts(1)) == 2


async def test_changing_server_count_does_not_mark_missing(setup):
    store, client, pool, settings = setup
    store.add_gift(1, saved_id=99, gift_id=44, title="Ранее видимый")
    client.pages[(1, "")] = page([gift(1)], count=2, next_offset="next")
    client.pages[(1, "next")] = page([gift(2), gift(3)], count=3)
    result = await crawl_gifts(pool, store, settings, "1")
    assert not result["results"][0]["complete"]
    assert all(row["presence"] == "visible" for row in store.list_gifts(1))


async def test_whole_network_depth_continues_beyond_eight(setup):
    store, client, pool, settings = setup
    for owner in range(1, 11):
        item = gift(owner)
        if owner < 10:
            item.from_id = types.PeerUser(owner + 1)
            next_user = types.User(id=owner + 1, access_hash=9, first_name=f"Человек {owner + 1}")
            response = page([item])
            response.users = [next_user]
        else:
            response = page([item])
        client.pages[(owner, "")] = response
    result = await crawl_gifts(pool, store, settings, "1", depth=8, max_profiles=20)
    assert result["profiles"] == 10
    assert len(store.list_gifts(10)) == 1

async def test_persistent_queue_resumes_after_interruption(setup):
    store, client, pool, settings = setup
    sender = types.User(id=2, access_hash=9, first_name="Даритель")
    item = gift(1)
    item.from_id = types.PeerUser(user_id=2)
    client.pages[(1, "")] = SimpleNamespace(gifts=[item], users=[sender], count=1, next_offset=None)
    client.pages[(2, "")] = page([])
    run_id = store.create_gift_crawl("1", 8, 50, 100)

    async def interrupt(progress):
        if progress["phase"].startswith("профиль 1"):
            raise RuntimeError("прерывание")

    with pytest.raises(RuntimeError, match="прерывание"):
        await crawl_gifts(pool, store, settings, "1", depth=8, max_profiles=50,
                          run_id=run_id, reporter=interrupt, skip_known=True)
    assert store.gift_crawl_counts(run_id)["pending"] == 1
    await crawl_gifts(pool, store, settings, "1", depth=8, max_profiles=50,
                      run_id=run_id, skip_known=True)
    assert store.gift_crawl_counts(run_id)["done"] == 2
    assert client.calls.count((1, "")) == 1


async def test_donor_without_user_object_is_preserved(setup):
    store, client, pool, settings = setup
    item = gift(1)
    item.from_id = types.PeerUser(user_id=2)
    client.pages[(1, "")] = page([item])
    client.pages[(2, "")] = page([])
    run_id = store.create_gift_crawl("1", 8, 50, 100)
    await crawl_gifts(pool, store, settings, "1", depth=8, max_profiles=50, run_id=run_id)
    assert store.gift_crawl_counts(run_id)["done"] == 2


def test_nft_latest_observation_replaces_old_chain_state(store: Store):
    store.upsert_nft_item("Test-1", on_chain=True, owner_address="wallet", owner_tg_id=5)
    store.upsert_nft_item("Test-1", on_chain=False, ownership_observed=True, owner_tg_id=6)
    row = store.nft_items()[0]
    assert row["on_chain"] == 0
    assert row["owner_address"] is None
    assert row["owner_tg_id"] == 6


def test_wallet_host_is_not_invented_owner(store: Store):
    from healdragg.gifts import _remember_meta, _unique_meta

    meta = _unique_meta(SimpleNamespace(
        slug="Test-9", owner_address="EQwallet", gift_address="EQgift",
        owner_id=None, host_id=types.PeerUser(user_id=11),
    ))
    _remember_meta(store, meta, owner_tg_id=11, owner_username="host", owner_name="Хост")
    row = store.nft_items()[0]
    assert row["on_chain"] == 1
    assert row["owner_tg_id"] is None
    assert row["owner_username"] is None
    assert row["owner_address"] == "EQwallet"


async def test_scan_saves_original_sticker_reference(setup):
    store, client, _, _ = setup
    document = types.Document(id=123, access_hash=1, file_reference=b"reference",
                              date=datetime(2025, 1, 1, tzinfo=timezone.utc),
                              mime_type="application/x-tgsticker", size=100, dc_id=1, attributes=[])
    ordinary = gift(1)
    ordinary.gift.sticker = document
    client.pages[(1, "")] = page([ordinary])
    await scan(setup, 1)
    assert store.gift_sticker(44) == bytes(document)


def test_unavailable_queue_retries_only_after_new_contact_info(store: Store):
    run_id = store.create_gift_crawl("1", 8, 100, 100)
    store.gift_crawl_enqueue(run_id, 20, 2)
    store.gift_crawl_finish_item(run_id, 20, "unavailable", "нет данных")
    store.gift_crawl_enqueue(run_id, 20, 1)
    assert store.gift_crawl_counts(run_id)["unavailable"] == 1
    store.gift_crawl_enqueue(run_id, 20, 1, "donor", 123)
    assert store.gift_crawl_next(run_id)["tg_id"] == 20
    assert store.gift_crawl_next(run_id)["level"] == 1
    store.gift_crawl_finish_item(run_id, 20, "unavailable", "нет доступа")
    store.gift_crawl_enqueue(run_id, 20, 1, "donor", 123)
    assert store.gift_crawl_counts(run_id)["unavailable"] == 1


async def test_known_heavy_profile_stays_deferred_until_completed(setup):
    store, client, pool, settings = setup
    store.defer_gift_profile(1, None, "Профиль", 2000)
    client.pages[(1, "")] = page([])
    run_id = store.create_gift_crawl("1", 8, 50, 100)
    await crawl_gifts(pool, store, settings, "1", depth=8, max_profiles=50,
                      run_id=run_id, skip_known=True)
    assert store.gift_crawl_counts(run_id)["deferred"] == 1
    assert client.calls == []
    store.finish_gift_snapshot(1, set())
    assert store.gift_crawl_counts(run_id)["deferred"] == 0
    assert store.gift_crawl_counts(run_id)["done"] == 1


async def test_duplicate_snapshot_is_not_marked_done(setup):
    store, client, pool, settings = setup
    client.pages[(1, "")] = page([gift(1), gift(1)], count=2)
    run_id = store.create_gift_crawl("1", 8, 50, 100)
    await crawl_gifts(pool, store, settings, "1", depth=8, max_profiles=50, run_id=run_id)
    assert store.gift_crawl_counts(run_id)["unavailable"] == 1
    assert not store.gift_profile_known(1)


async def test_scan_rejects_mismatched_connected_account(setup):
    store, client, _, settings = setup
    client.pages[(1, "")] = page([])
    account = SimpleNamespace(client=client, limiter=RateLimiter(0, 0), user_id=2)

    class Pool:
        async def first(self, sessions=None):
            return account

    with pytest.raises(ValueError, match="аккаунт изменился"):
        await crawl_gifts(Pool(), store, settings, "1", expected_session_user=1)
    assert client.calls == []


async def test_numeric_seed_is_resolved_as_user_id_and_can_refresh_known(setup):
    store, client, pool, settings = setup
    seen = []
    original = client.get_entity

    async def resolve(value):
        seen.append(value)
        return await original(value)

    client.get_entity = resolve
    client.pages[(1, "")] = page([gift(1)])
    await scan(setup, 1)
    client.pages[(1, "")] = page([gift(1), gift(2)])
    run = store.create_gift_crawl("1", 1, 1, 100, refresh_known=True)
    store.reopen()
    assert store.gift_crawl(run)["refresh_known"] == 1
    await crawl_gifts(pool, store, settings, "1", depth=1, max_profiles=1, run_id=run,
                      skip_known=not bool(store.gift_crawl(run)["refresh_known"]))
    assert seen == [1, 1]
    assert len(store.list_gifts(1)) == 2
