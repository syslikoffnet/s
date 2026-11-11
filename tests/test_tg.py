from __future__ import annotations

from types import SimpleNamespace

from telethon.tl import types
from telethon.tl.types import Channel

from healdragg.tg import (
    RateLimiter,
    channel_peer,
    chat_to_group,
    find_participant,
    group_link,
    history_peer,
    normalize_identifier,
    participant_status,
    target_input_peer,
)


def test_peers_for_supergroup_and_basic_group() -> None:
    supergroup = {"tg_id": 123, "kind": "supergroup", "is_megagroup": True, "access_hash": 55}
    basic = {"tg_id": 456, "kind": "group", "is_megagroup": False, "access_hash": None}

    channel = channel_peer(supergroup)
    assert isinstance(channel, types.InputPeerChannel)
    assert channel.channel_id == 123 and channel.access_hash == 55

    # для basic-групп — InputPeerChat, иначе getHistory/getFullChat падают
    history = history_peer(basic)
    assert isinstance(history, types.InputPeerChat)
    assert history.chat_id == 456

    assert isinstance(history_peer(supergroup), types.InputPeerChannel)
    assert isinstance(channel_peer(supergroup, 999), types.InputPeerChannel)


def test_target_input_peer_without_hash() -> None:
    peer = target_input_peer({"tg_user_id": 7})
    assert isinstance(peer, types.InputPeerUser)
    assert peer.user_id == 7 and peer.access_hash == 0
    with_hash = target_input_peer({"tg_user_id": 7, "access_hash": 1234})
    assert with_hash.access_hash == 1234


def test_normalize_identifier_variants() -> None:
    assert normalize_identifier("@durov") == "durov"
    assert normalize_identifier("https://t.me/durov/") == "durov"
    assert normalize_identifier("t.me/durov?single") == "durov"
    assert normalize_identifier("  7126917488  ") == "7126917488"


def test_participant_status_names() -> None:
    assert participant_status(SimpleNamespace()) == "member"
    assert participant_status(type("ChannelParticipantCreator", (), {})()) == "creator"
    assert participant_status(type("ChannelParticipantAdmin", (), {})()) == "admin"
    assert participant_status(type("ChannelParticipantBanned", (), {})()) == "left"


class FakeBasicClient:
    def __init__(self, user_ids: list[int]) -> None:
        self.user_ids = user_ids
        self.requests: list[object] = []

    async def __call__(self, request):
        self.requests.append(request)
        members = [SimpleNamespace(user_id=uid) for uid in self.user_ids]
        return SimpleNamespace(
            full_chat=SimpleNamespace(participants=SimpleNamespace(participants=members))
        )


async def test_basic_group_membership_by_full_chat() -> None:
    group = {"tg_id": 777, "kind": "group", "is_megagroup": False}
    target = {"tg_user_id": 42}
    limiter = RateLimiter(0, 0)

    client = FakeBasicClient([1, 42, 3])
    status, hash_value = await find_participant(client, group, target, limiter)
    assert status == "member" and hash_value is None
    assert isinstance(client.requests[0], object)

    client = FakeBasicClient([1, 2, 3])
    status, hash_value = await find_participant(client, group, target, limiter)
    assert status is None and hash_value is None


def test_chat_to_group_basic_chat() -> None:
    from telethon.tl.types import Chat

    chat = Chat(
        id=99,
        title="Basic",
        photo=None,
        participants_count=5,
        date=None,
        version=1,
    )
    info = chat_to_group(chat)
    assert info is not None
    assert info["kind"] == "group"
    assert info["is_megagroup"] is False
    assert info["members_count"] == 5


def test_group_link_variants() -> None:
    assert group_link(123, "durov", "channel") == "https://t.me/durov"
    assert group_link(-1001234567890, None, "supergroup") == "https://t.me/c/1234567890/1"
    assert group_link(-1001234567890, None, "channel") == "https://t.me/c/1234567890/1"
    assert group_link(123456789, None, "group") is None


def test_channel_to_group_keeps_link() -> None:
    entity = Channel(
        id=1234567890,
        title="Канал человека",
        photo=None,
        date=None,
        megagroup=False,
        access_hash=777,
        username=None,
    )
    info = chat_to_group(entity)
    assert info is not None
    assert info["kind"] == "channel"
    assert info["link"] == "https://t.me/c/1234567890/1"


def test_public_channel_link_from_username() -> None:
    entity = Channel(
        id=1234567890,
        title="Публичный",
        photo=None,
        date=None,
        megagroup=False,
        access_hash=777,
        username="public_channel",
    )
    info = chat_to_group(entity)
    assert info is not None
    assert info["link"] == "https://t.me/public_channel"
    assert info["is_public"] is True


async def test_cached_account_auth_is_awaited_and_invalid_session_is_removed(tmp_path):
    import pytest

    from healdragg.config import Settings
    from healdragg.db import Store
    from healdragg.tg import Account, SessionPool

    class Client:
        def __init__(self):
            self.authorized = True
            self.disconnected = False
            self.checks = 0

        def is_connected(self):
            return True

        async def is_user_authorized(self):
            self.checks += 1
            return self.authorized

        async def disconnect(self):
            self.disconnected = True

    store = Store(tmp_path / "db.sqlite")
    store.add_session("scan", "scan.session")
    pool = SessionPool(Settings(home=tmp_path, db_path=store.path, sessions_dir=tmp_path / "sessions"), store)
    client = Client()
    account = Account(name="scan", client=client)
    pool._accounts["scan"] = account
    assert await pool.connect("scan") is account
    assert client.checks == 1
    client.authorized = False
    with pytest.raises(RuntimeError, match="не авторизована"):
        await pool.connect("scan")
    assert client.disconnected and "scan" not in pool
    assert store.list_sessions()[0]["status"] == "needs_login"
    store.close()
