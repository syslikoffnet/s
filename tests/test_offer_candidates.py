from __future__ import annotations

import json

import pytest

from healdragg.db import Store
from healdragg.offers import candidates


def test_filters_use_all_observed_nfts_not_only_the_matching_gifts(store: Store):
    store.upsert_target(10, username="alice", first_name="Alice")
    store.update_target_profile(10, gender="female", gender_confidence=.8, country="RU")
    store.upsert_nft_item("Alpha-1", owner_tg_id=10, owner_username="alice", title="Alpha", value_usd=1000,
                          backdrop_name="Black", ownership_observed=True)
    store.upsert_nft_item("Beta-2", owner_tg_id=10, title="Beta", value_usd=100, on_chain=True,
                          ownership_observed=True)
    store.upsert_nft_item("Other-1", owner_tg_id=20, title="Other", value_usd=2000, backdrop_name="Black")
    store.upsert_nft_item("NoOwner-1", value_usd=5000, backdrop_name="Black")
    store.upsert_nft_item("Burned-1", owner_tg_id=10, burned=True)
    assert not candidates(store, {"max_nft": 1, "gender": "female"})["rows"]
    rows = candidates(store, {"max_nft": 2, "gender": "female", "country": "RU", "location": "telegram",
                              "backdrop": "BLACK", "min_value_usd_cents": 500})["rows"]
    assert [row["slug"] for row in rows] == ["Alpha-1"]
    assert rows[0]["observed_nft_count"] == 2
    assert candidates(store, {"location": "wallet"})["rows"][0]["slug"] == "Beta-2"


def test_legacy_attribute_fallback_ignores_previous_owner_and_bad_json(store: Store):
    store.upsert_nft_item("Apple-1", owner_tg_id=1)
    store.add_gift(1, saved_id=1, gift_id=1, slug="Apple-1", is_nft=True,
                   raw=json.dumps([{"type": "StarGiftAttributeBackdrop", "name": "Black"}]))
    assert candidates(store, {"backdrop": "black"})["rows"][0]["backdrop_name"] == "Black"
    store.upsert_nft_item("Apple-1", owner_tg_id=2, ownership_observed=True)
    assert candidates(store, {"backdrop": "black"})["rows"] == []
    store.add_gift(2, saved_id=2, gift_id=1, slug="Apple-1", is_nft=True, raw="{broken")
    assert candidates(store, {"backdrop": "black"})["rows"] == []


def test_pagination_and_input_validation(store: Store):
    for i in range(8):
        store.upsert_nft_item(f"A-{i}", owner_tg_id=i + 1)
    after = ""
    seen = []
    while True:
        result = candidates(store, {"limit": 3, "after": after})
        seen.extend(row["slug"] for row in result["rows"])
        if not result["next_after"]:
            break
        after = result["next_after"]
    assert seen == [f"A-{i}" for i in range(8)]
    for bad in ({"limit": True}, {"max_nft": -1}, {"location": "x"}, {"unknown": 1}, {"query": None}):
        with pytest.raises(ValueError):
            candidates(store, bad)
