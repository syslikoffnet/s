from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from telethon.tl import types

from healdragg.activity import collect_activity
from healdragg.config import Settings
from healdragg.db import Store
from healdragg.tg import RateLimiter
from healdragg.util import now_ts


class FakeMessage:
    def __init__(
        self,
        msg_id: int,
        text: str,
        *,
        date: int = 1_760_000_000,
        reply: FakeMessage | None = None,
        mention: int | None = None,
        media=None,
    ) -> None:
        self.id = msg_id
        self.message = text
        self.date = datetime.fromtimestamp(date, tz=timezone.utc)
        self.reply_to = SimpleNamespace(reply_to_msg_id=99) if reply else None
        self._reply = reply
        self.media = media
        self.views = 42
        self.entities = (
            [types.MessageEntityMentionName(offset=0, length=5, user_id=mention)]
            if mention
            else []
        )

    async def get_reply(self):
        return self._reply


class FakeClient:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.messages = messages

    def iter_messages(self, peer, from_user=None, limit=None):
        async def gen():
            for message in self.messages[:limit]:
                yield message

        return gen()


class FakeAccount:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.limiter = RateLimiter(0, 0)
        self.client = FakeClient(messages)


class FakePool:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.messages = messages

    async def connect(self, name):
        return FakeAccount(self.messages)


def _settings(tmp_path) -> Settings:
    return Settings(home=tmp_path, db_path=tmp_path / "a.db", sessions_dir=tmp_path / "s")


def _prepare(store: Store) -> dict:
    target = store.upsert_target(42, username="target_user")
    store.add_session("main", "/tmp/m.session", tg_user_id=1, status="active")
    session = store.get_session("main")
    group_pk = store.upsert_group(1001, title="Чат", username="chat_pub", kind="supergroup")
    store.link_session_group(int(session["id"]), group_pk, is_member=True, access_hash=7)
    store.upsert_target_group(int(target["id"]), group_pk, status="member", source="probe")
    return target


async def test_collect_activity_builds_edges(store: Store, tmp_path) -> None:
    target = _prepare(store)
    katya = types.User(id=777, first_name="Катя", username="katya")
    reply_message = SimpleNamespace(sender=katya)
    messages = [
        FakeMessage(1, "привет, как дела?", reply=reply_message),
        FakeMessage(2, "глянь @mentor", mention=888),
        FakeMessage(3, "просто сообщение"),
    ]

    stats = await collect_activity(
        FakePool(messages), store, _settings(tmp_path), target, days=3650, per_chat=50
    )
    assert stats["messages"] == 3
    assert stats["replies"] == 1
    assert stats["mentions"] == 1
    assert stats["peers"] == 2

    feed = store.activity_feed(int(target["id"]))
    assert len(feed) == 3
    assert feed[0]["link"] == "https://t.me/chat_pub"
    edges = {int(e["tg_id"]): e for e in store.target_edges(int(target["id"]))}
    assert edges[777]["replies"] == 1
    assert edges[888]["mentions"] == 1


async def test_collect_activity_is_idempotent(store: Store, tmp_path) -> None:
    target = _prepare(store)
    messages = [FakeMessage(1, "раз"), FakeMessage(2, "два")]
    pool = FakePool(messages)

    await collect_activity(pool, store, _settings(tmp_path), target, days=3650)
    await collect_activity(pool, store, _settings(tmp_path), target, days=3650)

    assert store.activity_stats(int(target["id"]))["messages"] == 2
    assert store.activity_feed(int(target["id"]))


def test_prune_activity(store: Store) -> None:
    target = store.upsert_target(43)
    group_pk = store.upsert_group(2002, title="Чат")
    old = 1_600_000_000
    store.add_message(int(target["id"]), group_pk, 1, date=old, text="старое")
    store.add_message(int(target["id"]), group_pk, 2, date=now_ts(), text="новое")
    assert store.prune_activity(int(target["id"]), days=30) == 1
    assert store.activity_stats(int(target["id"]))["messages"] == 1


async def test_incremental_activity_emits_alert_after_baseline(store: Store, tmp_path) -> None:
    target = _prepare(store)
    pool = FakePool([])
    await collect_activity(pool, store, _settings(tmp_path), target, incremental=True)
    assert store.count_alerts() == 0
    pool.messages.append(FakeMessage(5, "новое сообщение", date=now_ts()))
    stats = await collect_activity(pool, store, _settings(tmp_path), target, incremental=True)
    assert stats["messages"] == 1
    assert any(a["kind"] == "message" for a in store.list_alerts())
    await collect_activity(pool, store, _settings(tmp_path), target, incremental=True)
    assert sum(a["kind"] == "message" for a in store.list_alerts()) == 1


async def test_dialog_activity_records_only_observed_chat(store: Store, tmp_path) -> None:
    target = store.upsert_target(42, username="target_user")
    store.add_session("main", "/tmp/m.session", tg_user_id=1, status="active")

    class Client(FakeClient):
        async def iter_dialogs(self, limit=100):
            for number in (1001, 1002):
                yield SimpleNamespace(
                    entity=SimpleNamespace(id=number, title=f"Чат {number}", megagroup=True, access_hash=7),
                    is_group=True,
                    is_channel=True,
                )

        def iter_messages(self, peer, from_user=None, limit=None):
            async def generate():
                if getattr(peer, "channel_id", None) == 1001:
                    yield FakeMessage(1, "сообщение", date=now_ts())

            return generate()

    class Pool:
        async def connect(self, name):
            return SimpleNamespace(client=Client([]), limiter=RateLimiter(0, 0))

    await collect_activity(Pool(), store, _settings(tmp_path), target, incremental=True)
    groups = store.all_target_groups(int(target["id"]))
    assert [row["tg_id"] for row in groups] == [1001]
    assert store.activity_feed(int(target["id"]))[0]["text"] == "сообщение"


async def test_observed_message_does_not_overwrite_membership(store: Store, tmp_path) -> None:
    target = _prepare(store)
    await collect_activity(
        FakePool([FakeMessage(1, "сообщение", date=now_ts())]),
        store, _settings(tmp_path), target, incremental=True,
    )
    row = store.all_target_groups(int(target["id"]))[0]
    assert row["status"] == "member"
    assert row["source"] == "probe"
