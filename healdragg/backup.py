"""Резервные копии: база + сессии в один zip.

Всё, что пользователь собрал (цели, база чатов, NFT-инвентарь, подарки,
аккаунты), лежит в одном архиве. Его можно сохранить в файл, а после
переустановки — восстановить: аккаунты останутся на месте, ничего не потеряется.
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .db import Store

log = logging.getLogger("healdragg.backup")

KEEP = 5
FORMAT = 1


def _backup_dir(store: Store) -> Path:
    directory = store.path.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _session_files(store: Store) -> list[Path]:
    directory = store.path.parent / "sessions"
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix in {".session", ".db", ".json"}
    )


def create_backup(
    store: Store,
    *,
    keep: int = KEEP,
    label: str = "auto",
) -> dict[str, Any]:
    """Собирает zip: база, сессии, манифест. По умолчанию хранит 5 последних."""
    directory = _backup_dir(store)
    name = f"healdragg-{label}-{_stamp()}.zip"
    target = directory / name
    sessions = _session_files(store)
    tmp_db = Path(tempfile.mkstemp(prefix="healdragg-", suffix=".db")[1])
    try:
        store.backup_to(tmp_db)
        manifest = {
            "format": FORMAT,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "db_bytes": tmp_db.stat().st_size,
            "sessions": [path.name for path in sessions],
            "counts": _counts(store),
        }
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(tmp_db, "healdragg.db")
            for path in sessions:
                archive.write(path, f"sessions/{path.name}")
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    finally:
        tmp_db.unlink(missing_ok=True)
    _prune(directory, keep)
    stat = target.stat()
    return {
        "name": name,
        "path": str(target),
        "size": stat.st_size,
        "created_at": stat.st_mtime,
        "sessions": list(manifest["sessions"]),
        "counts": manifest["counts"],
    }


def _counts(store: Store) -> dict[str, int]:
    def scalar(sql: str) -> int:
        return int(store.scalar(sql, (), 0) or 0)

    return {
        "targets": scalar("SELECT COUNT(*) FROM targets"),
        "groups": scalar("SELECT COUNT(*) FROM groups"),
        "messages": scalar("SELECT COUNT(*) FROM messages"),
        "gifts": scalar("SELECT COUNT(*) FROM gifts"),
        "nft": scalar("SELECT COUNT(*) FROM nft_items"),
        "alerts": scalar("SELECT COUNT(*) FROM alerts"),
        "sessions": scalar("SELECT COUNT(*) FROM sessions"),
    }


def _prune(directory: Path, keep: int) -> None:
    keep = max(1, int(keep))
    files = sorted(directory.glob("healdragg-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)


def list_backups(store: Store) -> list[dict[str, Any]]:
    directory = _backup_dir(store)
    rows: list[dict[str, Any]] = []
    for path in sorted(
        directory.glob("healdragg-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        stat = path.stat()
        rows.append(
            {
                "name": path.name,
                "path": str(path),
                "size": stat.st_size,
                "created_at": stat.st_mtime,
            }
        )
    return rows


def latest_backup(store: Store) -> dict[str, Any] | None:
    rows = list_backups(store)
    return rows[0] if rows else None


def _validate(archive: zipfile.ZipFile) -> dict[str, Any]:
    names = set(archive.namelist())
    if "healdragg.db" not in names:
        raise ValueError("в архиве нет healdragg.db — это не копия Healdragg")
    for name in names:
        parts = Path(name).parts
        if name.startswith("/") or ".." in parts:
            raise ValueError("в архиве недопустимый путь")
    manifest: dict[str, Any] = {}
    if "manifest.json" in names:
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            manifest = {}
    return manifest


def restore_backup(payload: bytes, store: Store) -> dict[str, Any]:
    """Восстанавливает базу и сессии. Возвращает, что именно приехало."""
    if not payload:
        raise ValueError("пустой файл копии")
    if len(payload) > 400 * 1024 * 1024:
        raise ValueError("архив больше 400 МБ")
    work = Path(tempfile.mkdtemp(prefix="restore-"))
    sessions_dir = store.path.parent / "sessions"
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            manifest = _validate(archive)
            archive.extract("healdragg.db", work)
            session_names = [
                name
                for name in archive.namelist()
                if name.startswith("sessions/") and not name.endswith("/")
            ]
            for name in session_names:
                archive.extract(name, work)
        db_path = work / "healdragg.db"
        with db_path.open("rb") as handle:
            header = handle.read(16)
        if not header.startswith(b"SQLite format 3"):
            raise ValueError("файл базы в архиве повреждён")
        restored_sessions = 0
        for source in (work / "sessions").glob("*"):
            if not source.is_file():
                continue
            sessions_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, sessions_dir / source.name)
            restored_sessions += 1
        store.close()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{store.path}{suffix}").unlink(missing_ok=True)
        shutil.copy2(db_path, store.path)
        store.reopen()
        repaired = store.repair_session_paths(sessions_dir)
        return {
            "sessions": restored_sessions,
            "repaired_paths": repaired,
            "counts": _counts(store),
            "created_at": manifest.get("created_at"),
            "source": "upload",
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


__all__ = [
    "create_backup",
    "restore_backup",
    "list_backups",
    "latest_backup",
]
