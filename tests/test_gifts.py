from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.tl import types

from healdragg.config import Settings
from healdragg.db import Store
from healdragg.gifts import crawl_nft_chain
from healdragg.tg import RateLimiter


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
    assert summary["total"] == 1
    assert summary["nft"] == 0
    assert summary["owners"] == 1
    assert summary["senders"] == 1
    rows = store.list_gifts(1)
    assert len(rows) == 1
    assert rows[0]["from_username"] == "u2"
    assert rows[0]["owner_name"] == "User 1"


def test_nft_metadata(store: Store) -> None:
    store.add_gift(
        1,
        saved_id=500,
        gift_id=9,
        title="Plush Pepe",
        kind="unique",
        stars=1500,
        from_tg_id=2,
        date=100,
        is_nft=True,
        slug="PlushPepe-123",
        num=123,
        value_amount=1500,
        value_currency="TON",
        availability_issued=10,
        availability_total=50,
    )
    row = store.list_gifts(nft_only=True)[0]
    assert row["slug"] == "PlushPepe-123"
    assert row["num"] == 123
    assert row["value_amount"] == 1500
    assert row["value_currency"] == "TON"
    assert row["availability_total"] == 50


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


def test_nft_chain_split(store: Store) -> None:
    """NFT делятся на «в кошельке» и «в аккаунте Telegram»."""
    store.add_gift(1, saved_id=1, gift_id=5, title="Wallet", kind="unique", is_nft=True,
                   slug="Wallet-1", on_chain=True, owner_address="EQwallet", date=100)
    store.add_gift(1, saved_id=2, gift_id=5, title="Hosted", kind="unique", is_nft=True,
                   slug="Hosted-1", on_chain=False, date=200)
    assert len(store.list_gifts(nft_only=True, chain="wallet")) == 1
    assert len(store.list_gifts(nft_only=True, chain="telegram")) == 1
    summary = store.gift_summary()
    assert summary["nft_chain"] == 1
    assert summary["nft_hosted"] == 1
    assert store.list_gifts(nft_only=True, chain="wallet")[0]["on_chain"] == 1


def test_nft_inventory_rows(store: Store) -> None:
    assert store.upsert_nft_item(
        "PlushPepe-1", gift_id=9, num=1, title="Plush Pepe", owner_tg_id=777,
        owner_username="katya", owner_name="Катя", on_chain=True,
        owner_address="EQowner", value_amount=1500, value_currency="TON",
        availability_issued=10, availability_total=50,
    ) is True
    assert store.upsert_nft_item("PlushPepe-2", gift_id=9, num=2, title="Plush Pepe",
                                 owner_tg_id=777, on_chain=False) is True
    assert store.upsert_nft_item("PlushPepe-1", resell_amount=9000) is False

    chain_rows = store.nft_items(chain="wallet")
    assert len(chain_rows) == 1
    assert chain_rows[0]["resell_amount"] == 9000
    assert len(store.nft_items(chain="telegram")) == 1
    assert len(store.nft_items(q="katya")) == 1
    assert len(store.nft_items(q="777")) == 2

    summary = store.nft_summary()
    assert summary["total"] == 2
    assert summary["on_chain"] == 1
    assert summary["hosted"] == 1
    assert summary["owners"] == 1

    owners = store.nft_owners()
    assert owners and owners[0]["owner_tg_id"] == 777 and owners[0]["nft"] == 2


def test_nft_collections_progress(store: Store) -> None:
    store.upsert_nft_collection("PlushPepe", gift_id=9, title="Plush Pepe", total=3)
    row = store.next_nft_collection()
    assert row is not None
    assert row["slug_base"] == "PlushPepe" and row["cursor"] == 1
    store.advance_nft_collection("PlushPepe", cursor=2, scanned=1, owners=1, on_chain=1)
    row = store.next_nft_collection()
    assert row["cursor"] == 2 and row["scanned"] == 1
    store.mark_nft_collection_done("PlushPepe")
    assert store.next_nft_collection() is None
    assert store.nft_summary()["collections"] == 1


class FakeChainClient:
    """Отвечает на getUniqueStarGift и каталог подарков."""

    def __init__(self, items: dict[str, types.StarGiftUnique], catalog: list | None = None) -> None:
        self.items = items
        self.catalog = catalog or []

    async def __call__(self, request):
        name = type(request).__name__
        if name == "GetUniqueStarGiftRequest":
            gift = self.items.get(request.slug)
            if gift is None:
                raise RuntimeError("GIFT_NOT_FOUND")
            return gift
        if name == "GetStarGiftsRequest":
            return SimpleNamespace(gifts=self.catalog, chats=[], users=[])
        if name == "GetResaleStarGiftsRequest":
            gift = next(iter(self.items.values()), None)
            return SimpleNamespace(gifts=[gift] if gift else [], chats=[], users=[])
        raise AssertionError(f"неожиданный запрос {name}")


class FakeChainPool:
    def __init__(self, client: FakeChainClient) -> None:
        self.client = client

    def _account(self):
        return SimpleNamespace(client=self.client, limiter=RateLimiter(0, 0))

    async def connect(self, name):
        return self._account()

    async def first(self, names=None):
        return self._account()


def _unique(slug: str, num: int, *, owner_id: int | None, chain: bool) -> types.StarGiftUnique:
    return types.StarGiftUnique(
        id=num,
        gift_id=9,
        title="Plush Pepe",
        slug=slug,
        num=num,
        attributes=[],
        availability_issued=10,
        availability_total=12,
        owner_id=types.PeerUser(owner_id) if owner_id else None,
        owner_name=f"User {owner_id}" if owner_id else None,
        gift_address="EQgift" if chain else None,
        owner_address="EQowner" if chain else None,
        value_amount=1500,
        value_currency="TON",
    )


async def test_crawl_nft_chain_collects_owners(store: Store, tmp_path) -> None:
    items = {
        "PlushPepe-1": _unique("PlushPepe-1", 1, owner_id=777, chain=True),
        "PlushPepe-2": _unique("PlushPepe-2", 2, owner_id=888, chain=False),
    }
    settings = Settings(home=tmp_path, db_path=tmp_path / "c.db", sessions_dir=tmp_path / "s")
    store.upsert_nft_collection("PlushPepe", gift_id=9, title="Plush Pepe", total=2)

    result = await crawl_nft_chain(
        FakeChainPool(FakeChainClient(items)),
        store,
        settings,
        requests=2,
        discover=False,
    )
    assert result["items"] == 2
    assert result["new"] == 2
    assert result["owners"] == 2
    assert result["on_chain"] == 1
    assert result["hosted"] == 1
    assert result["inventory"]["total"] == 2
    assert store.next_nft_collection()["cursor"] == 3

    wallet = store.nft_items(chain="wallet")
    assert len(wallet) == 1 and wallet[0]["owner_tg_id"] == 777
    assert wallet[0]["owner_address"] == "EQowner"
