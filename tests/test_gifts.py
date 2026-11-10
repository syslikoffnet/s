from __future__ import annotations

from pathlib import Path

import pytest

from healdragg.db import Store


@pytest.fixture()
def store(tmp_path: Path):
    st = Store(tmp_path / "gifts.db")
    yield st
    st.close()


def _gift(store: Store, owner: int, sender: int, *, nft: bool = False, stars: int = 50, date: int = 100):
    return store.add_gift(
        owner,
        saved_id=date,
        gift_id=7,
        title="Rose" if not nft else "Plush Pepe",
        kind="unique" if nft else "regular",
        stars=stars,
        from_tg_id=sender,
        from_username=f"u{sender}",
        from_name=f"User {sender}",
        owner_username=f"u{owner}",
        owner_name=f"User {owner}",
        message="with love",
        date=date,
        is_nft=nft,
    )


def test_gift_upsert_and_summary(store: Store) -> None:
    assert _gift(store, 1, 2) is True
    assert _gift(store, 1, 2) is False
    summary = store.gift_summary()
    assert summary == {"total": 1, "nft": 0, "owners": 1, "senders": 1}
    rows = store.list_gifts(1)
    assert len(rows) == 1
    assert rows[0]["from_username"] == "u2"
    assert rows[0]["owner_name"] == "User 1"


def test_nft_filter(store: Store) -> None:
    _gift(store, 1, 2, nft=True, date=200)
    _gift(store, 1, 3, date=300)
    assert len(store.list_gifts(nft_only=True)) == 1
    assert store.gift_summary()["nft"] == 1


def test_gift_graph(store: Store) -> None:
    _gift(store, 2, 1, date=100)
    _gift(store, 3, 2, date=200)
    graph = store.gift_graph()
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 2
    seeded = store.gift_graph(seed=2)
    assert len(seeded["edges"]) == 2
    seeded_one = store.gift_graph(seed=1)
    assert len(seeded_one["edges"]) == 1


def test_touch_gifts_updates_target(store: Store) -> None:
    target = store.upsert_target(99, username="owner", first_name="Owner")
    _gift(store, 99, 1)
    assert store.touch_gifts(99) == 1
    fresh = store.get_target(99)
    assert fresh["gifts_count"] == 1
    assert fresh["gifts_updated_at"]
    assert target["tg_user_id"] == 99
