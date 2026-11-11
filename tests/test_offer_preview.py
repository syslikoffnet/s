from __future__ import annotations

import pytest

from healdragg.offers import preview_batch


def config(**changes):
    return {"currency": "gram", "usd_per_unit": "5", "markup_percent": "10",
            "max_per_offer": 10, "total_budget": 8, "max_offers": 10, **changes}


def test_preview_uses_decimal_limits_and_skips_ineligible(store):
    store.upsert_nft_item("A-1", owner_tg_id=1, owner_username="seller1", value_usd=1000)
    store.upsert_nft_item("A-2", owner_tg_id=1, owner_username="seller1", value_usd=1000)
    store.upsert_nft_item("B-1", owner_tg_id=2, owner_username="seller2", value_usd=2000)
    store.upsert_nft_item("C-1", owner_tg_id=3, owner_username="seller3")
    store.upsert_nft_item("D-1", owner_tg_id=4, owner_username="bad name", value_usd=1000)
    store.upsert_nft_item("E-1", owner_tg_id=5, owner_username="seller5", value_usd=100000)
    plan = preview_batch(store, config())
    assert [(r["slug"], r["amount"]) for r in plan["rows"]] == [("A-1", 3), ("B-1", 5)]
    assert plan["total_amount"] == 8
    assert plan["destination"] == "owner_private_chat"
    assert "личного чата владельца" in plan["notice"]
    assert plan["skipped"] == {"no_price": 1, "no_username": 1, "invalid_slug": 0,
                                "same_owner": 1, "over_per_offer": 1, "over_budget": 0}
    assert plan["rows"][0]["command"] == "/createoffer @seller1 https://t.me/nft/A-1 3 gram ru"
    assert not plan["truncated"]


def test_preview_scans_past_page_with_filtered_values(store):
    for i in range(105):
        store.upsert_nft_item(f"A-{i:03}", owner_tg_id=i + 1, owner_username="seller1")
    store.upsert_nft_item("B-1", owner_tg_id=1000, owner_username="seller2", value_usd=1)
    result = preview_batch(store, config(max_offers=1))
    assert result["examined"] == 106 and result["rows"][0]["amount"] == 1
    assert not result["truncated"]


def test_preview_caps_and_selection(store):
    for i in range(3):
        store.upsert_nft_item(f"A-{i}", owner_tg_id=i + 1, owner_username=f"seller{i}", value_usd=1000)
    result = preview_batch(store, config(max_offers=1))
    assert result["examined"] == 1 and result["truncated"]
    result = preview_batch(store, config(total_budget=3, one_per_owner=False, currency="star"))
    assert len(result["rows"]) == 1 and result["skipped"]["over_budget"] == 2


@pytest.mark.parametrize("changes", [
    {"usd_per_unit": ""}, {"usd_per_unit": "0"}, {"usd_per_unit": "NaN"},
    {"markup_percent": "-1"}, {"max_offers": True}, {"total_budget": 0},
    {"max_per_offer": 0}, {"currency": "USD"}, {"one_per_owner": 1},
    {"filters": {"limit": 5}}, {"filters": {"after": "A-1"}}, {"send": True},
])
def test_preview_rejects_unsafe_or_ambiguous_inputs(store, changes):
    with pytest.raises(ValueError):
        preview_batch(store, config(**changes))


def test_direct_nft_selection_is_exact(store):
    store.upsert_nft_item("Bear-1", owner_tg_id=1, owner_username="seller1", value_usd=100)
    store.upsert_nft_item("Bear-10", owner_tg_id=2, owner_username="seller2", value_usd=100)
    result = preview_batch(store, config(filters={"query": "Bear-1", "slug_exact": "Bear-1"}))
    assert [r["slug"] for r in result["rows"]] == ["Bear-1"]
