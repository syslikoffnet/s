from __future__ import annotations

from pathlib import Path

import pytest

from healdragg import scan as scan_mod
from healdragg.config import Settings
from healdragg.db import Store
from healdragg.scan import Reporter, bootstrap, global_search_target, scan_target


class FakePool:
    async def connect(self, name: str):
        raise AssertionError("сеть в этом тесте не нужна")

    async def close(self) -> None:
        pass


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(home=tmp_path, db_path=tmp_path / "s.db", sessions_dir=tmp_path / "sessions")


async def test_bootstrap_accepts_plain_callback(store: Store, settings: Settings, monkeypatch) -> None:
    store.add_session("main", "/tmp/x.session", tg_user_id=1, status="active")

    async def fake_sync(*args, **kwargs):
        return {"checked": 3, "groups": 2, "private": 0, "errors": 0}

    async def fake_search(*args, **kwargs):
        return {"checked": 1, "groups": 1, "public": 1, "errors": 0}

    monkeypatch.setattr(scan_mod, "sync_dialogs", fake_sync)
    monkeypatch.setattr(scan_mod, "search_public", fake_search)
    seen: list[dict] = []

    async def callback(state):
        seen.append(state)

    result = await bootstrap(store=store, pool=FakePool(), settings=settings, reporter=callback)
    assert result["groups"] == 3
    assert seen, "прогресс должен доходить до колбэка"
    scan_row = store.last_scan()
    assert scan_row["status"] == "done"
    # повторный bootstrap не падает на «reporter уже Reporter»
    result2 = await bootstrap(store=store, pool=FakePool(), settings=settings)
    assert result2["groups"] == 3


async def test_scan_target_prefers_own_session(store: Store, settings: Settings, monkeypatch) -> None:
    target = store.upsert_target(42, username="t")
    store.add_session("own", "/tmp/o.session", tg_user_id=42, status="active")
    calls: list[str] = []

    async def fake_own(pool, store_, target_, name, reporter=None):
        calls.append(name)
        gid = store_.upsert_group(500, title="чат", username="c", last_message_at=1)
        store_.upsert_target_group(int(target_["id"]), gid, status="member", source="own_session")
        return {"checked": 1, "found": 1, "meta": 1, "errors": 0}

    monkeypatch.setattr(scan_mod, "scan_own_dialogs", fake_own)
    result = await scan_target(FakePool(), store, settings, target)
    assert calls == ["own"]
    assert result["mode"] == "own_session"
    assert store.count_target_groups(int(target["id"])) == 1
    scan_row = store.last_scan(int(target["id"]))
    assert scan_row["status"] == "done"


async def test_scan_target_without_coverage_is_graceful(store: Store, settings: Settings) -> None:
    target = store.upsert_target(43)
    store.add_session("main", "/tmp/m.session", tg_user_id=1, status="active")
    result = await scan_target(FakePool(), store, settings, target, use_global_search=False)
    assert result["mode"] == "probe"
    assert result["found"] == 0


async def test_reporter_writes_progress(store: Store) -> None:
    scan_id = store.start_scan("probe", total=10)
    updates: list[dict] = []
    reporter = Reporter(store, scan_id=scan_id, callback=updates.append, min_interval=0)
    await reporter.update(done=5, total=10, found=2, phase="проверка", force=True)
    row = store.get_scan(scan_id)
    assert row["done"] == 5
    assert row["found"] == 2
    assert updates[-1]["phase"] == "проверка"
    await reporter.event("что-то пошло не так", "error")
    assert reporter.errors == 1
    assert store.recent_events(1)[0]["level"] == "error"


class JoinAccount:
    def __init__(self) -> None:
        from healdragg.tg import RateLimiter

        self.limiter = RateLimiter(0, 0)
        self.client = object()
        self.name = "main"


class JoinPool:
    async def connect(self, name: str) -> JoinAccount:
        return JoinAccount()


async def test_join_respects_daily_limit(store: Store, monkeypatch) -> None:
    store.add_session("main", "/tmp/m.session", tg_user_id=1, status="active")
    for i in range(3):
        store.upsert_group(700 + i, title=f"g{i}", username=f"g{i}")

    async def fake_join(client, group, limiter):
        return True

    monkeypatch.setattr(scan_mod, "join_public_group", fake_join)

    store.incr_daily("join:main", 2)
    result = await scan_mod.join_discovered(JoinPool(), store, "main", limit=2)
    assert result["joined"] == 0
    assert result["skipped"] == 1

    # другой день — бюджет снова доступен
    import time as _time

    tomorrow = _time.strftime("%Y-%m-%d", _time.localtime(_time.time() + 86400))
    assert store.daily_count("join:main", day=tomorrow) == 0
    result = await scan_mod.join_discovered(JoinPool(), store, "main", limit=2)
    # сегодняшний лимит всё ещё исчерпан
    assert result["joined"] == 0


async def test_join_increments_counter(store: Store, monkeypatch) -> None:
    store.add_session("main", "/tmp/m2.session", tg_user_id=2, status="active")
    for i in range(2):
        store.upsert_group(800 + i, title=f"j{i}", username=f"j{i}")

    async def fake_join(client, group, limiter):
        return True

    monkeypatch.setattr(scan_mod, "join_public_group", fake_join)
    result = await scan_mod.join_discovered(JoinPool(), store, "main", limit=5)
    assert result["joined"] == 2
    assert store.daily_count("join:main") == 2


async def test_own_scan_marks_left(store: Store, monkeypatch) -> None:
    from types import SimpleNamespace

    from telethon.tl.types import Channel

    from healdragg.tg import RateLimiter

    target = store.upsert_target(55, username="t")
    store.add_session("own", "/tmp/o2.session", tg_user_id=55, status="active")
    store.upsert_group(901, title="keep", username="k")
    gone = store.upsert_group(902, title="gone", username="g")
    store.upsert_target_group(int(target["id"]), gone, status="member", source="own_session")

    channel = Channel(
        id=901,
        title="keep",
        photo=None,
        date=None,
        username="k",
        megagroup=True,
        access_hash=123,
        participants_count=10,
    )
    dialog = SimpleNamespace(
        entity=channel,
        message=SimpleNamespace(date=__import__("datetime").datetime(2026, 11, 1, 12)),
    )

    class FakeClient:
        async def iter_dialogs(self, limit=None, wait_time=None):
            yield dialog

    class OwnAccount:
        user_id = 55
        limiter = RateLimiter(0, 0)
        client = FakeClient()

    class OwnPool:
        async def connect(self, name: str) -> OwnAccount:
            return OwnAccount()

    async def fake_meta(client, entity, limiter):
        return 12, 1_760_000_000

    monkeypatch.setattr(scan_mod, "fetch_history_meta", fake_meta)
    stats = await scan_mod.scan_own_dialogs(OwnPool(), store, target, "own")
    assert stats["found"] == 1
    assert stats["left"] == 1
    assert store.count_target_groups(int(target["id"]), "left") == 1
    rows = {r["title"]: r["status"] for r in store.all_target_groups(int(target["id"]))}
    assert rows["keep"] == "member"
    assert rows["gone"] == "left"


class _SearchResult:
    def __init__(self, chats):
        self.chats = chats


class _ChannelAccount:
    """Фейковый клиент: глобальный поиск возвращает канал человека."""

    def __init__(self, chats):
        from healdragg.tg import RateLimiter

        self.limiter = RateLimiter(0, 0)
        self.client = self
        self._chats = chats

    async def __call__(self, request):
        return _SearchResult(self._chats)


class _ChannelPool:
    def __init__(self, chats):
        self._chats = chats

    async def connect(self, name):
        return _ChannelAccount(self._chats)


async def test_global_search_checks_person_channel(store: Store) -> None:
    from telethon.tl.types import Channel

    target = store.upsert_target(45, username="channel_owner")
    store.add_session("main", "/tmp/m.session", tg_user_id=1, status="active")
    channel = Channel(
        id=1234567890,
        title="Канал человека",
        photo=None,
        date=None,
        megagroup=False,
        access_hash=321,
        username=None,
    )

    result = await global_search_target(_ChannelPool([channel]), store, target)
    assert result["channels"] == 0
    assert result["found"] == 0
    assert store.all_target_groups(int(target["id"])) == []


async def test_global_search_requires_target_author_and_preserves_membership(store: Store) -> None:
    from types import SimpleNamespace

    from telethon.tl import types

    target = store.upsert_target(45, username="person")
    store.add_session("main", "/tmp/m.session", tg_user_id=1, status="active")
    channel = types.Channel(id=123, title="Channel", photo=None, date=None,
                            broadcast=True, access_hash=321)
    unrelated = types.Channel(id=124, title="Other", photo=None, date=None,
                              broadcast=True, access_hash=322)
    group_pk = store.upsert_group(123, title="Channel", kind="channel")
    store.upsert_target_group(int(target["id"]), group_pk, status="member", source="probe")
    messages = [
        SimpleNamespace(from_id=types.PeerUser(45), peer_id=types.PeerChannel(123)),
        SimpleNamespace(from_id=types.PeerUser(46), peer_id=types.PeerChannel(124)),
    ]
    account = _ChannelAccount([channel, unrelated])

    class Client:
        async def __call__(self, request):
            return SimpleNamespace(chats=[channel, unrelated], messages=messages)

    account.client = Client()

    class Pool:
        async def connect(self, name):
            return account

    result = await global_search_target(Pool(), store, target)
    assert result["found"] == 1
    rows = store.all_target_groups(int(target["id"]))
    assert len(rows) == 1
    assert rows[0]["status"] == "member"
    assert rows[0]["source"] == "probe"
