"""Активность цели: находим её сообщения даже без скана и при скрытых участниках."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.tl import types

from healdragg.activity import _dialog_chats, _global_target_messages
from healdragg.db import Store


class _FakeDialog:
    def __init__(self, entity: SimpleNamespace, *, is_channel: bool = False) -> None:
        self.entity = entity
        self.is_group = True
        self.is_channel = is_channel


class _FakeAccount:
    def __init__(self, client: object) -> None:
        self.client = client
        self.limiter = None


class _FakePool:
    def __init__(self, account: _FakeAccount) -> None:
        self._account = account

    async def connect(self, name: str) -> _FakeAccount:
        return self._account


def _entity(tg_id: int, title: str, **extra: object) -> SimpleNamespace:
    base = {
        "id": tg_id,
        "title": title,
        "access_hash": 12345,
        "username": None,
        "megagroup": True,
        "participants_count": 100,
    }
    base.update(extra)
    return SimpleNamespace(**base)


def test_dialog_chats_work_without_scan(tmp_path: Path) -> None:
    store = Store(tmp_path / "db.sqlite")
    target = store.upsert_target(777000, username="durov")
    entity = _entity(555, "Общий чат")

    class Client:
        async def iter_dialogs(self, limit: int = 100):  # noqa: ANN201
            yield _FakeDialog(entity)

    coverage: dict[str, dict[int, int]] = {}
    try:
        rows = asyncio_run(_dialog_chats(_FakePool(_FakeAccount(Client())), store, target, coverage))
        assert [row["tg_id"] for row in rows] == [555]
        assert coverage["main"][555] == 12345
        groups = store.all_target_groups(int(target["id"]))
        # Диалог нашего аккаунта сам по себе не доказывает связь цели с чатом.
        assert groups == []
    finally:
        store.close()


def test_global_search_stores_messages_from_unknown_chat(tmp_path: Path) -> None:
    store = Store(tmp_path / "db.sqlite")
    target = store.upsert_target(777000, username="durov", access_hash=999)
    channel = types.Channel(
        id=321,
        title="Чужой канал",
        photo=types.ChatPhotoEmpty(),
        date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        megagroup=True,
        access_hash=4321,
        username="stranger",
    )
    message = types.Message(
        id=10,
        peer_id=types.PeerChannel(321),
        date=datetime.now(timezone.utc),
        message="привет из общей группы",
    )
    result = SimpleNamespace(messages=[message], chats=[channel], users=[])

    class Client:
        async def get_entity(self, key: object) -> SimpleNamespace:  # noqa: ARG002
            return SimpleNamespace(id=777000, access_hash=999)

        async def __call__(self, request: object) -> SimpleNamespace:  # noqa: ARG002
            return result

    try:
        stats = asyncio_run(
            _global_target_messages(_FakePool(_FakeAccount(Client())), store, target, cutoff=0)
        )
        assert stats["found"] == 1
        assert stats["stored"] == 1
        assert stats["chats"] == 1
        feed = store.activity_feed(int(target["id"]))
        assert feed and feed[0]["text"] == "привет из общей группы"
        # Чат попал в связи цели — на экране больше не «0 групп».
        assert store.activity_stats(int(target["id"]))["chats"] == 1
    finally:
        store.close()


def test_global_search_survives_missing_access_hash(tmp_path: Path) -> None:
    store = Store(tmp_path / "db.sqlite")
    target = store.upsert_target(777001, username="nobody")

    class Client:
        async def get_entity(self, key: object) -> SimpleNamespace:  # noqa: ARG002
            raise ValueError("нет доступа")

        async def __call__(self, request: object) -> SimpleNamespace:  # noqa: ARG002
            raise AssertionError("не должно дойти до запроса")

    try:
        stats = asyncio_run(
            _global_target_messages(_FakePool(_FakeAccount(Client())), store, target, cutoff=0)
        )
        assert stats == {"found": 0, "chats": 0, "stored": 0}
    finally:
        store.close()


def asyncio_run(coro):  # noqa: ANN001, ANN201
    import asyncio

    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменяем список сессий, чтобы не зависеть от файлов на диске."""
    original = Store.session_names

    def fake(self: Store) -> list[str]:  # noqa: ARG001
        return ["main"]

    monkeypatch.setattr(Store, "session_names", fake, raising=False)
    monkeypatch.setattr(Store, "_orig_session_names", original, raising=False)
