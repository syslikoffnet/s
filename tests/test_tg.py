from __future__ import annotations

from types import SimpleNamespace

from telethon.tl import types

from healdragg.tg import (
    RateLimiter,
    channel_peer,
    chat_to_group,
    find_participant,
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
