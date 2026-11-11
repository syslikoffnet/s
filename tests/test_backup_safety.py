"""Проверки целостности и аварийного отката резервных копий."""
from __future__ import annotations

import io
import os
import sqlite3
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from healdragg.api import Job, create_app
from healdragg.backup import create_backup, recover_from_latest, recover_pending_restore, restore_backup
from healdragg.config import Settings
from healdragg.db import Store

HEADERS = {"X-Api-Token": "secret"}


def archive_with_db(data: bytes, extras: dict[str, bytes] | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("healdragg.db", data)
        for name, content in (extras or {}).items():
            archive.writestr(name, content)
    return out.getvalue()


def test_corrupt_database_does_not_touch_existing_data(tmp_path):
    store = Store(tmp_path / "app.db")
    store.upsert_target(10, username="original")
    try:
        with pytest.raises(ValueError):
            restore_backup(archive_with_db(b"SQLite format 3\x00" + b"x" * 8192), store)
        assert store.get_target(10)["username"] == "original"
        assert not (tmp_path / ".healdragg-restore").exists()
    finally:
        store.close()


def test_recovery_after_process_dies_mid_restore(tmp_path):
    old = Store(tmp_path / "old.db")
    old.upsert_target(10, username="original")
    new = Store(tmp_path / "new.db")
    new.upsert_target(20, username="restored")
    new_backup = create_backup(new)
    archive_path = Path(new_backup["path"])
    old.close()
    new.close()
    script = '''
import os, sys
from pathlib import Path
import healdragg.backup as b
from healdragg.db import Store
store = Store(Path(sys.argv[1]))
original = b._atomic_copy
def crash(source, target):
    original(source, target)
    if target == store.path:
        os._exit(73)
b._atomic_copy = crash
b.restore_backup(Path(sys.argv[2]).read_bytes(), store)
'''
    proc = subprocess.run([sys.executable, "-c", script, str(tmp_path / "old.db"), str(archive_path)], check=False)
    assert proc.returncode == 73
    assert (tmp_path / ".healdragg-restore").exists()
    assert recover_pending_restore(tmp_path / "old.db", tmp_path / "sessions")
    store = Store(tmp_path / "old.db")
    try:
        assert store.get_target(10)["username"] == "original"
        assert store.get_target(20) is None
    finally:
        store.close()


def test_restore_rolls_back_on_error_after_replacing_db(tmp_path, monkeypatch):
    import healdragg.backup as backup

    store = Store(tmp_path / "app.db")
    store.upsert_target(10, username="original")
    other = Store(tmp_path / "other.db")
    other.upsert_target(20, username="replacement")
    raw = Path(create_backup(other)["path"]).read_bytes()
    other.close()
    original = backup._atomic_copy
    failed = False

    def fail_once(source, target):
        nonlocal failed
        original(source, target)
        if target == store.path and not failed:
            failed = True
            raise OSError("disk error")

    monkeypatch.setattr(backup, "_atomic_copy", fail_once)
    try:
        with pytest.raises(OSError, match="disk error"):
            restore_backup(raw, store)
        assert store.get_target(10)["username"] == "original"
        assert store.get_target(20) is None
        assert not (tmp_path / ".healdragg-restore").exists()
    finally:
        store.close()


def test_backup_uses_sqlite_snapshot_for_wal_and_custom_directory(tmp_path):
    sessions = tmp_path / "different-location"
    sessions.mkdir()
    db = sessions / "main.session"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.commit()
    conn.execute("INSERT INTO t VALUES ('only-in-wal')")
    conn.commit()
    store = Store(tmp_path / "app.db")
    try:
        created = create_backup(store, sessions_dir=sessions)
        with zipfile.ZipFile(created["path"]) as archive:
            data = archive.read("sessions/main.session")
        restored = tmp_path / "snap.db"
        restored.write_bytes(data)
        with sqlite3.connect(restored) as result:
            assert result.execute("SELECT v FROM t").fetchone()[0] == "only-in-wal"
    finally:
        store.close()
        conn.close()


def test_missing_session_in_archive_is_not_matched_to_old_local_account(tmp_path):
    source = Store(tmp_path / "source.db")
    source.add_session("main", "main.session", tg_user_id=42)
    raw = Path(create_backup(source)["path"]).read_bytes()
    source.close()
    dest = Store(tmp_path / "dest.db")
    try:
        restore_backup(raw, dest)
        assert dest.get_session("main")["status"] == "needs_login"
        assert dest.session_names() == []
    finally:
        dest.close()


def test_corrupt_latest_archive_does_not_replace_original(tmp_path):
    store = Store(tmp_path / "app.db")
    previous = create_backup(store)
    store.close()
    bad = tmp_path / "backups" / "healdragg-bad.zip"
    bad.write_bytes(archive_with_db(b"SQLite format 3\x00" + b"x" * 2000))
    os.utime(bad, (Path(previous["path"]).stat().st_mtime + 10,) * 2)
    (tmp_path / "app.db").write_bytes(b"broken database")
    assert recover_from_latest(tmp_path, tmp_path / "app.db") == previous["name"]
    assert any(p.read_bytes() == b"broken database" for p in tmp_path.glob("app.db.broken-*"))
    result = Store(tmp_path / "app.db")
    result.close()


def test_restore_rejected_when_task_is_running(tmp_path):
    settings = Settings(home=tmp_path, db_path=tmp_path / "app.db", sessions_dir=tmp_path / "sessions",
                        api_token="secret", auto_backup=False)
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.hd.jobs["busy"] = Job(key="busy", kind="test")
        response = client.post("/api/backup/restore", headers=HEADERS, content=b"anything")
        assert response.status_code == 409
        assert "задачи" in response.text
        app.state.hd.jobs.clear()


def test_restore_blocks_parallel_requests_until_io_finishes(tmp_path, monkeypatch):
    settings = Settings(home=tmp_path, db_path=tmp_path / "app.db", sessions_dir=tmp_path / "sessions",
                        api_token="secret", auto_backup=False)
    entered = threading.Event()
    release = threading.Event()

    def restore(*args):
        entered.set()
        assert release.wait(5)
        return {"counts": {}}

    monkeypatch.setattr("healdragg.api.restore_backup", restore)
    app = create_app(settings)
    with TestClient(app) as client:
        response = []
        thread = threading.Thread(target=lambda: response.append(
            client.post("/api/backup/restore", headers=HEADERS, content=b"data")))
        thread.start()
        assert entered.wait(5)
        assert client.get("/api/stats", headers=HEADERS).status_code == 409
        release.set()
        thread.join(5)
        assert response[0].status_code == 200
        assert client.get("/api/stats", headers=HEADERS).status_code == 200


def test_restored_tasks_are_paused_and_missing_session_marked(tmp_path):
    source = Store(tmp_path / "source.db")
    source.add_session("chosen", "chosen.session", tg_user_id=7)
    crawl = source.create_gift_crawl("@owner", 1, 5, 100)
    chain = source.create_nft_chain_run(20, False, None, None, "chosen", 7)
    source.save_heavy_task(1, "@owner", "chosen", True, 60, 7)
    payload = Path(create_backup(source)["path"]).read_bytes()
    source.close()
    dest = Store(tmp_path / "dest.db")
    try:
        restore_backup(payload, dest)
        assert dest.gift_crawl(crawl)["status"] == "paused"
        assert dest.nft_chain_run(chain)["status"] == "paused"
        assert dest.heavy_task(1)["status"] == "error"
        assert dest.get_session("chosen")["status"] == "needs_login"
    finally:
        dest.close()


def test_archive_with_ambiguous_session_path_is_rejected(tmp_path):
    store = Store(tmp_path / "app.db")
    db = tmp_path / "snap.db"
    store.backup_to(db)
    try:
        payload = archive_with_db(db.read_bytes(), {"sessions/./main.session": b"bad"})
        with pytest.raises(ValueError, match="имя"):
            restore_backup(payload, store)
    finally:
        store.close()


def test_process_dies_after_replacing_session_rolls_back_both(tmp_path):
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_sessions = old_dir / "sessions"
    new_sessions = new_dir / "sessions"
    old_sessions.mkdir(parents=True)
    new_sessions.mkdir(parents=True)
    for root, content in ((old_sessions, "old-key"), (new_sessions, "new-key")):
        with sqlite3.connect(root / "main.session") as conn:
            conn.execute("CREATE TABLE marker (value TEXT)")
            conn.execute("INSERT INTO marker VALUES (?)", (content,))
    old = Store(old_dir / "app.db")
    old.upsert_target(10, username="original")
    new = Store(new_dir / "app.db")
    new.upsert_target(20, username="replacement")
    archive_path = Path(create_backup(new)["path"])
    old.close()
    new.close()
    script = '''
import os, sys
from pathlib import Path
import healdragg.backup as b
from healdragg.db import Store
store = Store(Path(sys.argv[1]))
original = b._atomic_copy
def crash(source, target):
    original(source, target)
    if target.name == "main.session":
        os._exit(74)
b._atomic_copy = crash
b.restore_backup(Path(sys.argv[2]).read_bytes(), store)
'''
    proc = subprocess.run([sys.executable, "-c", script, str(old_dir / "app.db"), str(archive_path)], check=False)
    assert proc.returncode == 74
    assert recover_pending_restore(old_dir / "app.db", old_sessions)
    store = Store(old_dir / "app.db")
    try:
        assert store.get_target(10)["username"] == "original"
        assert store.get_target(20) is None
        with sqlite3.connect(old_sessions / "main.session") as conn:
            assert conn.execute("SELECT value FROM marker").fetchone()[0] == "old-key"
    finally:
        store.close()
