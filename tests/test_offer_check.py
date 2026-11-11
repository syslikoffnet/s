from __future__ import annotations

from types import SimpleNamespace

import pytest
from telethon import functions, types

from healdragg import offer_check
from healdragg.tg import Account, RateLimiter


class Client:
    def __init__(self):
        self.me = types.User(id=777, premium=True)
        self.user = types.User(id=42, username="seller42", access_hash=9)
        self.peer = types.PeerUser(42)
        self.gift = types.StarGiftUnique(id=1, gift_id=2, title="Bear", slug="Bear-1", num=1,
                                         attributes=[], availability_issued=1, availability_total=10,
                                         owner_id=types.PeerUser(42), value_usd_amount=1000)
        self.requests = []

    async def get_me(self):
        return self.me

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.contacts.ResolveUsernameRequest):
            return SimpleNamespace(peer=self.peer, users=[self.user])
        if isinstance(request, functions.payments.GetUniqueStarGiftRequest):
            return SimpleNamespace(gift=self.gift)
        raise AssertionError(f"unexpected Telegram request: {type(request)}")


@pytest.fixture
def scenario(store, monkeypatch):
    client = Client()
    account = Account("writer", client, limiter=RateLimiter(0, 0))
    store.add_session("writer", "writer.session", purpose="offers", tg_user_id=777)
    store.upsert_nft_item("Bear-1", owner_tg_id=42, owner_username="seller42", value_usd=500)

    class Pool:
        async def connect(self, name, *, allow_reserved=False):
            assert name == "writer" and allow_reserved
            return account

    async def status(actual):
        assert actual is account
        return {"status": "connected", "missing_rights": [], "all_chats": True, "bot_id": 123}

    monkeypatch.setattr(offer_check.business, "status", status)
    return store, Pool(), client, {"session": "writer", "slug": "Bear-1", "seller_id": 42}


async def test_live_check_is_read_only_and_reports_facts(scenario):
    store, pool, client, body = scenario
    before = store.query_one("SELECT * FROM nft_items WHERE slug=?", ("Bear-1",))
    result = await offer_check.check(store, pool, body)
    assert result["owner_matches"] and result["username_matches"] and result["premium"]
    assert result["value_usd_cents"] == 1000 and result["account_id"] == 777
    assert result["bot"]["status"] == "connected" and result["checked_at"] > 0
    assert "не отправлена" in result["notice"]
    assert [type(r) for r in client.requests] == [functions.contacts.ResolveUsernameRequest,
                                                    functions.payments.GetUniqueStarGiftRequest]
    assert store.query_one("SELECT * FROM nft_items WHERE slug=?", ("Bear-1",)) == before


@pytest.mark.parametrize("field,value", [("owner_id", types.PeerUser(43)), ("owner_id", None),
                                          ("owner_id", types.PeerChannel(42)), ("burned", True),
                                          ("owner_address", "wallet"), ("gift_address", "address"),
                                          ("host_id", types.PeerUser(1))])
async def test_uncertain_owner_is_not_confirmed(scenario, field, value):
    store, pool, client, body = scenario
    setattr(client.gift, field, value)
    assert not (await offer_check.check(store, pool, body))["owner_matches"]


@pytest.mark.parametrize("change", ["wrong_id", "wrong_peer", "renamed", "deleted", "bot", "minimal"])
async def test_username_not_confirmed_for_wrong_or_partial_user(scenario, change):
    store, pool, client, body = scenario
    if change == "wrong_id":
        client.user.id = 43
    elif change == "wrong_peer":
        client.peer = types.PeerUser(43)
    elif change == "renamed":
        client.user.username = "newname"
    elif change == "deleted":
        client.user.deleted = True
    elif change == "bot":
        client.user.bot = True
    else:
        client.user.min = True
    assert not (await offer_check.check(store, pool, body))["username_matches"]


async def test_no_price_premium_or_business_remain_explicit(scenario, monkeypatch):
    store, pool, client, body = scenario
    client.me.premium = False
    client.gift.value_usd_amount = None

    async def status(account):
        return {"status": "not_connected", "missing_rights": [], "all_chats": False}

    monkeypatch.setattr(offer_check.business, "status", status)
    result = await offer_check.check(store, pool, body)
    assert result["value_usd_cents"] is None and not result["premium"]
    assert result["bot"]["status"] == "not_connected"


@pytest.mark.parametrize("change", [
    {"seller_id": True}, {"seller_id": 0}, {"seller_id": 41}, {"slug": "other-1"},
    {"slug": "bad link"}, {"session": "missing"}, {"send": True},
])
async def test_rejects_invalid_or_stale_input_before_network(scenario, change):
    store, pool, client, body = scenario
    with pytest.raises(ValueError):
        await offer_check.check(store, pool, {**body, **change})
    assert not client.requests


async def test_wrong_gift_is_rejected(scenario):
    store, pool, client, body = scenario
    client.gift.slug = "Bear-2"
    with pytest.raises(ValueError, match="не вернул"):
        await offer_check.check(store, pool, body)
