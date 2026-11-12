from __future__ import annotations

import asyncio

import pytest

from healdragg import offer_batch, offer_check


def body(**plan_changes):
    plan = {"currency": "star", "usd_per_unit": "0.02", "markup_percent": "10",
            "max_per_offer": 600, "total_budget": 1100, "max_offers": 10}
    plan.update(plan_changes)
    return {"session": "writer", "plan": plan}


@pytest.fixture
def batch_store(store):
    store.add_session("writer", "writer.session", purpose="offers", tg_user_id=777)
    for i in range(1, 4):
        store.upsert_nft_item(f"Bear-{i}", owner_tg_id=i, owner_username=f"seller{i}", value_usd=1000)
    return store


def verified(item, value=1000, **changes):
    return {"slug": item["slug"], "seller_id": item["seller_id"], "seller_username": "seller1",
            "account_id": 777, "owner_matches": True, "username_matches": True, "premium": True,
            "bot": {"status": "connected", "missing_rights": [], "all_chats": True},
            "value_usd_cents": value, "checked_at": 1, **changes}


async def test_batch_checks_fresh_values_and_budget_without_sending(batch_store, monkeypatch):
    seen = []

    async def check(store, pool, item):
        seen.append(item)
        return verified(item, 1100 if item["seller_id"] == 1 else 1000)

    monkeypatch.setattr(offer_check, "check", check)
    run_id = offer_batch.create(batch_store, body())
    await offer_batch.run(batch_store, None, run_id, asyncio.Lock())
    result = offer_batch.detail(batch_store, run_id)
    assert result["status"] == "done"
    assert [r["status"] for r in result["rows"]] == ["skipped", "checked"]
    assert "лимита" in result["rows"][0]["result"]["reasons"][0]
    assert result["rows"][1]["result"]["batch_total"] == 550
    assert len(seen) == 2 and all(set(row) == {"slug", "session", "seller_id"} for row in seen)


async def test_batch_skips_unverified_and_excess_over_total_budget(batch_store, monkeypatch):
    async def check(store, pool, item):
        return verified(item, 1000, username_matches=item["seller_id"] != 1)

    monkeypatch.setattr(offer_check, "check", check)
    run_id = offer_batch.create(batch_store, body(total_budget=1650))
    await offer_batch.run(batch_store, None, run_id, asyncio.Lock())
    result = offer_batch.detail(batch_store, run_id)
    assert [r["status"] for r in result["rows"]] == ["skipped", "checked", "checked"]
    assert result["rows"][-1]["result"]["batch_total"] == 1100


async def test_batch_new_prices_can_exceed_total_even_when_original_plan_fits(batch_store, monkeypatch):
    async def check(store, pool, item):
        return verified(item, 1090)

    monkeypatch.setattr(offer_check, "check", check)
    run_id = offer_batch.create(batch_store, body(max_per_offer=700))
    await offer_batch.run(batch_store, None, run_id, asyncio.Lock())
    rows = offer_batch.detail(batch_store, run_id)["rows"]
    assert [r["status"] for r in rows] == ["checked", "skipped"]
    assert rows[0]["result"]["amount"] == 600
    assert rows[1]["result"]["batch_total"] == 600


async def test_exception_or_cancel_stops_without_retry(batch_store, monkeypatch):
    started = asyncio.Event()

    async def blocked(store, pool, item):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(offer_check, "check", blocked)
    run_id = offer_batch.create(batch_store, body())
    task = asyncio.create_task(offer_batch.run(batch_store, None, run_id, asyncio.Lock()))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = offer_batch.detail(batch_store, run_id)
    assert result["status"] == "stopped" and result["rows"][0]["status"] == "pending"

    async def fail(store, pool, item):
        raise TimeoutError("offline")

    monkeypatch.setattr(offer_check, "check", fail)
    new_id = offer_batch.create(batch_store, body())
    await offer_batch.run(batch_store, None, new_id, asyncio.Lock())
    result = offer_batch.detail(batch_store, new_id)
    assert result["status"] == "error" and result["error"] == "offline"
    assert all(row["status"] == "pending" for row in result["rows"])


def test_atomic_single_run_and_interruption_recovery(batch_store):
    run_id = offer_batch.create(batch_store, body())
    with pytest.raises(ValueError, match="уже выполняется"):
        offer_batch.create(batch_store, body())
    batch_store.execute("UPDATE offer_check_items SET status='checking' WHERE run_id=? AND position=0", (run_id,))
    offer_batch.interrupt_running(batch_store)
    assert offer_batch.detail(batch_store, run_id)["status"] == "interrupted"
    assert offer_batch.detail(batch_store, run_id)["rows"][0]["status"] == "pending"
    assert offer_batch.create(batch_store, body()) != run_id


@pytest.mark.parametrize("plan", [{"usd_per_unit": "0"}, {"total_budget": 0}, {"max_offers": 101}, {"send": True}])
def test_batch_reuses_strict_plan_validation(batch_store, plan):
    with pytest.raises(ValueError):
        offer_batch.create(batch_store, body(**plan))


def test_rejects_client_injected_rows_and_non_offer_account(batch_store):
    with pytest.raises(ValueError):
        offer_batch.create(batch_store, {**body(), "rows": []})
    batch_store.add_session("scan", "scan.session", purpose="scan")
    with pytest.raises(ValueError):
        offer_batch.create(batch_store, {**body(), "session": "scan"})


@pytest.mark.parametrize("changes", [
    {"owner_matches": False}, {"username_matches": False}, {"premium": False},
    {"account_id": 1}, {"value_usd_cents": None},
    {"bot": {"status": "not_connected", "missing_rights": [], "all_chats": True}},
    {"bot": {"status": "connected", "missing_rights": ["reply"], "all_chats": True}},
    {"bot": {"status": "connected", "missing_rights": [], "all_chats": False}},
])
async def test_missing_prerequisites_are_skipped(batch_store, monkeypatch, changes):
    async def check(store, pool, item):
        return verified(item, **changes)

    monkeypatch.setattr(offer_check, "check", check)
    run_id = offer_batch.create(batch_store, body(max_offers=1))
    await offer_batch.run(batch_store, None, run_id, asyncio.Lock())
    item = offer_batch.detail(batch_store, run_id)["rows"][0]
    assert item["status"] == "skipped" and item["result"]["reasons"]
    assert item["result"]["batch_total"] == 0


def test_persistence_and_no_automatic_resume_after_reopen(tmp_path):
    from healdragg.db import Store

    path = tmp_path / "data.db"
    store = Store(path)
    store.add_session("writer", "writer.session", purpose="offers")
    store.upsert_nft_item("Bear-1", owner_tg_id=1, owner_username="seller1", value_usd=1000)
    run_id = offer_batch.create(store, body())
    store.execute("UPDATE offer_check_items SET status='checking' WHERE run_id=?", (run_id,))
    store.close()
    store = Store(path)
    try:
        offer_batch.interrupt_running(store)
        result = offer_batch.detail(store, run_id)
        assert result["status"] == "interrupted"
        assert result["rows"][0]["status"] == "pending"
    finally:
        store.close()
