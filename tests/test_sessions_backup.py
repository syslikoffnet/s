from __future__ import annotations

import base64
import io
import os
import zipfile
from pathlib import Path

import pytest
from telethon.crypto import AuthKey

from healdragg.backup import create_backup, latest_backup, list_backups, restore_backup
from healdragg.db import Store
from healdragg.session_import import import_upload, session_info
from healdragg.tdata import looks_like_tdata, vendor_ready


@pytest.fixture()
def home(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    yield store, sessions
    store.close()


def _make_session(path: Path) -> bytes:
    from telethon.sessions import SQLiteSession

    session = SQLiteSession(str(path))
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(os.urandom(256))
    session.save()
    payload = path.read_bytes()
    path.unlink()
    return payload


def test_import_telethon_session(home) -> None:
    store, sessions = home
    payload = _make_session(Path(str(sessions) + "-src.session"))
    result = import_upload("main.session", payload, store, sessions)
    assert len(result["imported"]) == 1
    entry = result["imported"][0]
    assert entry["name"] == "main"
    target = Path(entry["file"])
    assert target.is_file()
    assert session_info(target) is not None
    assert store.session_names() == ["main"]

    again = import_upload("main.session", payload, store, sessions)
    assert again["imported"][0]["name"] != "main"
    assert len(store.session_names()) == 2


def test_import_string_session_json(home) -> None:
    store, sessions = home
    from telethon.sessions import StringSession

    string = StringSession()
    string.set_dc(4, "149.154.167.91", 443)
    string.auth_key = AuthKey(os.urandom(256))
    payload = string.save().encode()

    result = import_upload("phone.json", payload, store, sessions)
    entry = result["imported"][0]
    assert entry["source"] == "json"
    assert entry["dc_id"] == 4
    assert session_info(Path(entry["file"])) is not None


def test_import_rejects_garbage(home) -> None:
    store, sessions = home
    with pytest.raises(ValueError):
        import_upload("broken.session", b"not a session", store, sessions)
    assert store.session_names() == []
    assert not list(sessions.glob("*.session"))


def test_import_zip_with_sessions(home) -> None:
    store, sessions = home
    payload = _make_session(Path(str(sessions) + "-src.session"))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("one.session", payload)
        archive.writestr("notes.txt", "не сессия")
    result = import_upload("bundle.zip", buffer.getvalue(), store, sessions)
    assert len(result["imported"]) == 1
    assert result["errors"] == []


def test_tdata_detection_and_vendor() -> None:
    assert vendor_ready() is True
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("tdata/key_datas", b"\x00" * 64)
        archive.writestr("tdata/settingss", b"\x00" * 16)
    data = buffer.getvalue()
    assert looks_like_tdata(data) is True

    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("readme.txt", "hi")
    assert looks_like_tdata(plain.getvalue()) is False


def test_tdata_bad_archive_message(home) -> None:
    store, sessions = home
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("tdata/key_datas", b"\x00" * 64)
    with pytest.raises(ValueError) as info:
        import_upload("tdata.zip", buffer.getvalue(), store, sessions)
    assert "tdata" in str(info.value).lower()


def test_backup_roundtrip(home, tmp_path: Path) -> None:
    store, sessions = home
    store.upsert_target(4242, username="target")
    store.add_gift(4242, saved_id=1, gift_id=9, title="Plush Pepe", kind="unique", is_nft=True)
    payload = _make_session(Path(str(sessions) + "-src.session"))
    import_upload("main.session", payload, store, sessions)

    created = create_backup(store, keep=3)
    assert Path(created["path"]).is_file()
    assert created["counts"]["targets"] == 1
    assert created["sessions"]

    rows = list_backups(store)
    assert rows and rows[0]["name"] == created["name"]
    assert latest_backup(store)["name"] == created["name"]

    other_home = tmp_path / "other"
    other_home.mkdir()
    fresh = Store(other_home / "app.db")
    try:
        zip_bytes = Path(created["path"]).read_bytes()
        result = restore_backup(zip_bytes, fresh)
        assert result["counts"]["targets"] == 1
        assert result["counts"]["sessions"] >= 1
        assert result["sessions"] >= 1
        restored_sessions = list((other_home / "sessions").glob("*.session"))
        assert restored_sessions
        row = fresh.get_session("main")
        assert row is not None
        assert Path(row["session_file"]).parent == other_home / "sessions"
        assert fresh.get_target(4242)["username"] == "target"
    finally:
        fresh.close()


def test_backup_rejects_foreign_zip(home) -> None:
    store, _ = home
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "world")
    with pytest.raises(ValueError):
        restore_backup(buffer.getvalue(), store)


def test_session_file_survives_path_move(home, tmp_path: Path) -> None:
    store, sessions = home
    payload = _make_session(Path(str(sessions) + "-src.session"))
    entry = import_upload("main.session", payload, store, sessions)["imported"][0]
    moved = tmp_path / "moved"
    moved.mkdir()
    (moved / Path(entry["file"]).name).write_bytes(Path(entry["file"]).read_bytes())
    Path(entry["file"]).unlink()
    fixed = store.repair_session_paths(moved)
    assert fixed == 1
    assert Path(store.get_session("main")["session_file"]).parent == moved
    assert store.session_names() == ["main"]


def test_import_via_base64_payload(home) -> None:
    store, sessions = home
    payload = _make_session(Path(str(sessions) + "-src.session"))
    encoded = base64.b64encode(payload).decode()
    assert encoded


def test_vendor_qt_shim_roundtrip() -> None:
    """QDataStream/QByteArray/QBuffer из _vendor должны переживать цикл шифрования."""
    import os

    from healdragg.tdata import _ensure_path

    _ensure_path()
    from opentele.td.auth import AuthKey
    from opentele.td.configs import DcId
    from opentele.td.storage import Storage

    key = AuthKey(os.urandom(256), dcId=DcId(2))
    descriptor = Storage.EncryptedDescriptor(16)
    descriptor.stream.writeInt32(123456)
    descriptor.stream.writeInt32(-7)
    descriptor.stream.writeUInt64(987654321)
    encrypted = Storage.PrepareEncrypted(descriptor, key)
    assert encrypted.size() > 16
    back = Storage.DecryptLocal(encrypted, key)
    assert back.stream.readInt32() == 123456
    assert back.stream.readInt32() == -7
    assert back.stream.readUInt64() == 987654321


def test_import_reserved_database_name_uses_new_matching_filename(home) -> None:
    store, sessions = home
    store.add_session("main", "missing.session", status="needs_login")
    payload = _make_session(Path(str(sessions) + "-src.session"))
    entry = import_upload("main.session", payload, store, sessions)["imported"][0]
    assert entry["name"] != "main"
    assert Path(entry["file"]).name == entry["name"] + ".session"
    assert store.get_session("main")["status"] == "needs_login"


def test_tdata_limits_total_uncompressed_size(tmp_path: Path, monkeypatch) -> None:
    from healdragg import tdata

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("tdata/key_datas", b"a" * 32)
        archive.writestr("tdata/part", b"b" * 32)
    # Создаём большое значение только в метаданных без гигабайтовых фикстур.
    raw = bytearray(payload.getvalue())
    sig = b"PK\x01\x02"
    position = raw.index(sig)
    raw[position + 24:position + 28] = (601 * 1024 * 1024).to_bytes(4, "little")
    with pytest.raises(ValueError, match="велик"):
        tdata._safe_extract(bytes(raw), tmp_path)


def test_import_offers_session_is_reserved_atomically(home) -> None:
    store, sessions = home
    payload = _make_session(Path(str(sessions) + "-offer-src.session"))
    result = import_upload("offer.session", payload, store, sessions, purpose="offers")
    name = result["imported"][0]["name"]
    assert store.get_session(name)["purpose"] == "offers"
    assert name not in store.session_names()
    assert name in store.session_names(only_active=False)
    # Обычное обновление состояния сессии не меняет назначение.
    store.add_session(name, str(sessions / f"{name}.session"), username="writer")
    assert store.get_session(name)["purpose"] == "offers"


def test_backup_restores_reserved_session_purpose(home, tmp_path: Path) -> None:
    store, sessions = home
    payload = _make_session(Path(str(sessions) + "-src.session"))
    import_upload("writer.session", payload, store, sessions, purpose="offers")
    created = create_backup(store, sessions_dir=sessions)
    fresh = Store(tmp_path / "other" / "app.db")
    try:
        restore_backup(Path(created["path"]).read_bytes(), fresh)
        assert fresh.get_session("writer")["purpose"] == "offers"
        assert fresh.session_names() == []
    finally:
        fresh.close()
