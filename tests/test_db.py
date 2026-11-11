from __future__ import annotations

import json

from healdragg.db import Store
from healdragg.demo import DEMO_TARGET, clear_demo, seed_demo


def test_session_crud_and_cascade(store: Store) -> None:
    sid = store.add_session("main", "/tmp/main.session", tg_user_id=1, username="me")
    assert store.session_names() == ["main"]
    store.link_session_group(sid, store.upsert_group(100, title="A", username="a"))
    store.link_session_group(sid, store.upsert_group(101, title="B", username="b"))
    assert store.coverage()["groups"] == 2
    assert len(store.session_groups("main")) == 2

    store.remove_session("main")
    assert store.session_names() == []
    # группы остаются, привязки — нет
    assert store.count_groups() == 2
    assert store.coverage()["groups"] == 0


def test_group_upsert_deduplicates(store: Store) -> None:
    first = store.upsert_group(10, title="Старое", username="old", members_count=5)
    second = store.upsert_group(10, title="Новое", members_count=7, discovered_from="dialogs")
    assert first == second
    assert store.count_groups() == 1
    row = store.get_group(10)
    assert row["title"] == "Новое"
    assert row["username"] == "old"  # не затёрли None
    assert row["members_count"] == 7
    assert row["discovered_from"] == "dialogs"


def test_public_flag_from_username(store: Store) -> None:
    store.upsert_group(1, title="pub", username="pub")
    store.upsert_group(2, title="priv")
    assert store.count_groups(public_only=True) == 1
    assert store.get_group(2)["is_public"] == 0


def test_target_groups_pagination_and_order(store: Store) -> None:
    target = store.upsert_target(777, username="t")
    tp = int(target["id"])
    for idx, (title, last) in enumerate([("old", 100), ("new", 500), ("mid", 300)]):
        gid = store.upsert_group(1000 + idx, title=title, username=f"u{idx}", last_message_at=last)
        store.upsert_target_group(tp, gid, status="member", source="probe")
    rows = store.list_target_groups(tp, page=1, size=10)
    assert [r["title"] for r in rows] == ["new", "mid", "old"]
    page2 = store.list_target_groups(tp, page=2, size=2)
    assert [r["title"] for r in page2] == ["old"]
    assert store.count_target_groups(tp) == 3


def test_target_stats(store: Store) -> None:
    target = store.upsert_target(777)
    tp = int(target["id"])
    rows = [
        (1, "member", True),
        (2, "admin", True),
        (3, "left", True),
        (4, "creator", False),
    ]
    for tg_id, status, public in rows:
        gid = store.upsert_group(tg_id, title=f"g{tg_id}", username=f"g{tg_id}" if public else None)
        store.upsert_target_group(tp, gid, status=status)
    stats = store.target_stats(tp)
    assert stats["total"] == 4
    assert stats["admin"] == 2
    assert stats["left_count"] == 1
    assert stats["private"] == 1
    assert stats["public"] == 3


def test_group_status_updates(store: Store) -> None:
    target = store.upsert_target(1)
    gid = store.upsert_group(5, title="g")
    store.upsert_target_group(int(target["id"]), gid, status="member")
    store.mark_target_group(int(target["id"]), gid, "left")
    assert store.count_target_groups(int(target["id"]), "left") == 1


def test_scans(store: Store) -> None:
    sid = store.start_scan("probe", sessions=["main"], total=10)
    store.update_scan(sid, done=4, found=2)
    scan = store.get_scan(sid)
    assert scan["status"] == "running"
    assert scan["done"] == 4
    store.finish_scan(sid, status="done", stats={"ok": True})
    scan = store.get_scan(sid)
    assert scan["status"] == "done"
    assert json.loads(scan["stats"]) == {"ok": True}
    assert store.last_scan()["id"] == sid


def test_search(store: Store) -> None:
    store.upsert_group(1, title="Музыкальный чат", username="music")
    store.upsert_group(2, title="Книжный клуб", username="books")
    assert [r["tg_id"] for r in store.search_groups("музык")] == [1]
    assert [r["tg_id"] for r in store.search_groups("books")] == [2]
    assert store.search_groups("") == []


def test_export_import(store: Store, tmp_path) -> None:
    seed_demo(store, total=20)
    target = store.get_target(DEMO_TARGET)
    payload = store.export_target(int(target["id"]))
    assert len(payload["groups"]) == 20

    other = Store(tmp_path / "other.db")
    try:
        other.import_target(payload)
        imported = other.get_target(DEMO_TARGET)
        assert other.count_target_groups(int(imported["id"])) == 20
    finally:
        other.close()


def test_clear_demo(store: Store) -> None:
    seed_demo(store, total=10)
    assert store.count_groups() == 10
    clear_demo(store)
    assert store.count_groups() == 0
    assert store.count_targets() == 0


def test_stats_and_integrity(store: Store) -> None:
    seed_demo(store, total=5)
    stats = store.stats()
    assert stats["groups"] == 5
    assert stats["targets"] == 1
    assert stats["target_groups"] == 5
    assert store.integrity_check() == "ok"
    store.optimize()
    assert store.size_bytes() > 0


def test_daily_counters(store: Store) -> None:
    assert store.daily_count("join:main", day="2026-11-09") == 0
    assert store.incr_daily("join:main", day="2026-11-09") == 1
    assert store.incr_daily("join:main", 4, day="2026-11-09") == 5
    assert store.daily_count("join:main", day="2026-11-09") == 5
    assert store.daily_count("join:main", day="2026-11-10") == 0


def test_mark_missing_groups_as_left(store: Store) -> None:
    target = store.upsert_target(321)
    tp = int(target["id"])
    keep = store.upsert_group(1, title="остался", username="keep")
    gone = store.upsert_group(2, title="удалён", username="gone")
    probed = store.upsert_group(3, title="из каталога", username="probe")
    store.upsert_target_group(tp, keep, status="member", source="own_session")
    store.upsert_target_group(tp, gone, status="member", source="own_session")
    store.upsert_target_group(tp, probed, status="member", source="probe")

    changed = store.mark_target_groups_left(tp, [keep], source="own_session")
    assert changed == 1
    assert store.count_target_groups(tp, "left") == 1
    statuses = {r["title"]: r["status"] for r in store.all_target_groups(tp)}
    assert statuses["остался"] == "member"
    assert statuses["удалён"] == "left"
    # источник probe не тронут
    assert statuses["из каталога"] == "member"
    # повторный вызов ничего не меняет
    assert store.mark_target_groups_left(tp, [keep], source="own_session") == 0


def test_group_links_are_stored(store: Store) -> None:
    public = store.upsert_group(1001, title="Публичный", username="pubchat", kind="supergroup")
    private = store.upsert_group(-1001234567890, title="Приватный", kind="channel")
    basic = store.upsert_group(555, title="Basic", kind="group")

    assert store.get_group_by_pk(public)["link"] == "https://t.me/pubchat"
    assert store.get_group_by_pk(private)["link"] == "https://t.me/c/1234567890/1"
    assert store.get_group_by_pk(basic)["link"] is None


def test_target_groups_include_links(store: Store) -> None:
    target = store.upsert_target(42, username="someone")
    gid = store.upsert_group(1002, title="Чат", username="chat_username", kind="supergroup")
    store.upsert_target_group(int(target["id"]), gid, status="member")
    rows = store.list_target_groups(int(target["id"]))
    assert rows[0]["link"] == "https://t.me/chat_username"


def test_migration_backfills_links(tmp_path) -> None:
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER NOT NULL UNIQUE,
            access_hash INTEGER,
            title TEXT NOT NULL DEFAULT '',
            username TEXT,
            kind TEXT NOT NULL DEFAULT 'group',
            is_public INTEGER NOT NULL DEFAULT 0,
            is_megagroup INTEGER NOT NULL DEFAULT 0,
            members_count INTEGER,
            messages_total INTEGER,
            last_message_at INTEGER,
            meta_checked_at INTEGER,
            discovered_from TEXT DEFAULT 'manual',
            discovered_by TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO groups(tg_id, title, username, kind, created_at, updated_at) "
        "VALUES(-1001234567890, 'Приватный', NULL, 'channel', 1, 1)"
    )
    conn.execute(
        "INSERT INTO groups(tg_id, title, username, kind, created_at, updated_at) "
        "VALUES(-1009876543210, 'Публичный', 'pubchat', 'supergroup', 1, 1)"
    )
    conn.commit()
    conn.close()

    migrated = Store(path)
    try:
        assert migrated.get_group(-1001234567890)["link"] == "https://t.me/c/1234567890/1"
        assert migrated.get_group(-1009876543210)["link"] == "https://t.me/pubchat"
    finally:
        migrated.close()


def test_legacy_sessions_migrate_to_scan(tmp_path) -> None:
    import sqlite3

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE sessions (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, session_file TEXT NOT NULL,
            tg_user_id INTEGER, username TEXT, first_name TEXT, phone TEXT,
            status TEXT NOT NULL DEFAULT 'new', last_error TEXT, added_at INTEGER NOT NULL,
            last_used_at INTEGER
        )""")
        conn.execute("INSERT INTO sessions (name, session_file, status, added_at) VALUES ('legacy', 'legacy.session', 'active', 1)")
    db = Store(path)
    try:
        assert db.get_session("legacy")["purpose"] == "scan"
        assert db.session_names() == ["legacy"]
    finally:
        db.close()
