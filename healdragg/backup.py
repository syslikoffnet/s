"""Резервные копии: база + сессии в один zip.

Всё, что пользователь собрал (цели, база чатов, NFT-инвентарь, подарки,
аккаунты), лежит в одном архиве. Его можно сохранить в файл, а после
переустановки — восстановить: аккаунты останутся на месте, ничего не потеряется.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from time import time
from typing import Any, BinaryIO
from uuid import uuid4

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


def _session_files(store: Store, sessions_dir: Path | None = None) -> list[Path]:
    directory = Path(sessions_dir) if sessions_dir is not None else store.path.parent / "sessions"
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix in {".session", ".db", ".json"}
    )


def _sqlite_snapshot(source: Path, dest: Path) -> None:
    """Копирует и WAL, не читая работающую SQLite-сессию как обычный файл."""
    with source.open("rb") as fh:
        header = fh.read(16)
    if header != b"SQLite format 3\x00":
        if source.suffix == ".json":
            shutil.copyfile(source, dest)
            return
        raise ValueError(f"Повреждённый файл сессии: {source.name}")
    src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
        if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError(f"Повреждённый файл сессии: {source.name}")
    finally:
        dst.close()
        src.close()


def create_backup(
    store: Store,
    *,
    keep: int = KEEP,
    label: str = "auto",
    sessions_dir: Path | None = None,
) -> dict[str, Any]:
    """Публикует только завершённый архив; живые SQLite-сессии копирует через SQLite."""
    if not label.isascii() or not label.replace("-", "").isalnum():
        raise ValueError("Недопустимая метка копии")
    directory = _backup_dir(store)
    name = f"healdragg-{label}-{_stamp()}-{uuid4().hex[:8]}.zip"
    target = directory / name
    sessions = _session_files(store, sessions_dir)
    with tempfile.TemporaryDirectory(prefix=".backup-", dir=directory) as tmp:
        work = Path(tmp)
        tmp_db = work / "healdragg.db"
        store.backup_to(tmp_db)
        snapshots = work / "sessions"
        snapshots.mkdir()
        for path in sessions:
            if path.is_symlink():
                raise ValueError(f"Символическая ссылка вместо сессии: {path.name}")
            _sqlite_snapshot(path, snapshots / path.name)
        manifest = {
            "format": FORMAT,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "db_bytes": tmp_db.stat().st_size,
            "sessions": [path.name for path in sessions],
            "counts": _counts(store),
        }
        partial = work / "backup.zip"
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(tmp_db, "healdragg.db")
            for path in sessions:
                archive.write(snapshots / path.name, f"sessions/{path.name}")
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        os.chmod(partial, 0o600)
        _sync_file(partial)
        os.replace(partial, target)
        _sync_dir(directory)
    _prune(directory, keep)
    result = target.stat()
    return {"name": name, "path": str(target), "size": result.st_size,
            "created_at": result.st_mtime, "sessions": list(manifest["sessions"]),
            "counts": manifest["counts"]}


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


MAX_ARCHIVE_BYTES = 400 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
MAX_ENTRIES = 2000
JOURNAL = ".healdragg-restore"


def _validate(archive: zipfile.ZipFile) -> dict[str, Any]:
    infos = archive.infolist()
    names = [item.filename for item in infos]
    if len(names) > MAX_ENTRIES or len(names) != len(set(names)):
        raise ValueError("В архиве слишком много файлов или есть повторяющиеся имена")
    if "healdragg.db" not in names:
        raise ValueError("в архиве нет healdragg.db — это не копия Healdragg")
    if sum(item.file_size for item in infos) > MAX_EXTRACTED_BYTES:
        raise ValueError("Содержимое копии слишком велико")
    for info in infos:
        name = info.filename
        if "\\" in name or name.startswith("/") or ".." in Path(name).parts:
            raise ValueError("в архиве недопустимый путь")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError("Ссылки в копии не поддерживаются")
        if name.startswith("sessions/") and not info.is_dir():
            if (name != f"sessions/{Path(name).name}" or
                    Path(name).suffix not in {".session", ".db", ".json"}):
                raise ValueError("Недопустимое имя файла сессии")
    manifest: dict[str, Any] = {}
    if "manifest.json" in names:
        if archive.getinfo("manifest.json").file_size > 1024 * 1024:
            raise ValueError("Манифест копии слишком велик")
        try:
            parsed = json.loads(archive.read("manifest.json").decode("utf-8"))
            if isinstance(parsed, dict):
                manifest = parsed
        except (ValueError, UnicodeDecodeError):
            pass
    return manifest


def _validate_database(path: Path) -> None:
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("База в копии повреждена")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"targets", "sessions", "groups"}.issubset(tables):
                raise ValueError("В копии нет основных таблиц Healdragg")
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError("База в копии повреждена") from exc


def _sync_file(path: Path) -> None:
    with path.open("rb") as fh:
        os.fsync(fh.fileno())


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    try:
        with source.open("rb") as reader, os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, target)
        _sync_dir(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def recover_pending_restore(db_path: Path, sessions_dir: Path) -> bool:
    """Откатывает прерванную замену файлов до открытия базы и клиентов Telegram."""
    journal = db_path.parent / JOURNAL
    if not journal.exists():
        return False
    meta = json.loads((journal / "restore.json").read_text(encoding="utf-8"))
    names = meta["sessions"]
    if not isinstance(names, dict) or any(
        not isinstance(name, str) or Path(name).name != name or Path(name).suffix not in {".session", ".db", ".json"}
        or not isinstance(existed, bool) for name, existed in names.items()
    ):
        raise ValueError("Повреждён журнал восстановления; данные не изменены")
    if not (journal / "committed").exists():
        _remove_sidecars(db_path)
        _atomic_copy(journal / "old.db", db_path)
        for name, existed in names.items():
            target = sessions_dir / name
            _remove_sidecars(target)
            if existed:
                _atomic_copy(journal / "old-sessions" / name, target)
            else:
                target.unlink(missing_ok=True)
        if sessions_dir.exists():
            _sync_dir(sessions_dir)
        log.warning("прерванное восстановление откачено")
    shutil.rmtree(journal)
    _sync_dir(db_path.parent)
    return True


def restore_backup(payload: bytes | BinaryIO, store: Store, sessions_dir: Path | None = None) -> dict[str, Any]:
    """Проверяет архив, затем заменяет файлы с журналом отката после сбоя."""
    stream = io.BytesIO(payload) if isinstance(payload, bytes) else payload
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    if size == 0:
        raise ValueError("пустой файл копии")
    if size > MAX_ARCHIVE_BYTES:
        raise ValueError("архив больше 400 МБ")
    sessions_dir = Path(sessions_dir) if sessions_dir is not None else store.path.parent / "sessions"
    journal = store.path.parent / JOURNAL
    if journal.exists():
        raise RuntimeError("Остался журнал восстановления; перезапустите движок")
    # Временная папка и журнал на том же разделе, что и база.
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=store.path.parent) as tmp:
        work = Path(tmp)
        new = work / "new"
        new.mkdir()
        try:
            with zipfile.ZipFile(stream) as archive:
                manifest = _validate(archive)
                selected = ["healdragg.db"] + [i.filename for i in archive.infolist()
                            if i.filename.startswith("sessions/") and not i.is_dir()]
                for name in selected:
                    archive.extract(name, new)
        except (zipfile.BadZipFile, RuntimeError, EOFError, OSError) as exc:
            raise ValueError("Не удалось прочитать архив копии") from exc
        _validate_database(new / "healdragg.db")
        files = sorted((new / "sessions").glob("*")) if (new / "sessions").exists() else []
        checked = work / "checked"
        checked.mkdir()
        for source in files:
            _sqlite_snapshot(source, checked / source.name)
        old = work / "old-sessions"
        old.mkdir()
        names: dict[str, bool] = {}
        for source in files:
            target = sessions_dir / source.name
            if target.is_symlink():
                raise ValueError(f"Символическая ссылка вместо сессии: {source.name}")
            names[source.name] = target.exists()
            if target.exists():
                _sqlite_snapshot(target, old / source.name)
        store.backup_to(work / "old.db")
        (work / "restore.json").write_text(json.dumps({"sessions": names}), encoding="utf-8")
        for path in (work / "old.db", work / "restore.json", *old.iterdir(), *checked.iterdir(), new / "healdragg.db"):
            _sync_file(path)
        for path in (old, checked, new, work):
            _sync_dir(path)
        # TemporaryDirectory ожидает свою папку, поэтому для журнала выделяем отдельный подкаталог.
        stage = work / "journal"
        stage.mkdir()
        for name in ("old.db", "restore.json", "old-sessions", "checked", "new"):
            os.replace(work / name, stage / name)
        _sync_dir(stage)
        os.replace(stage, journal)
        _sync_dir(store.path.parent)
        store.close()
        try:
            _remove_sidecars(store.path)
            _atomic_copy(journal / "new" / "healdragg.db", store.path)
            for name in names:
                target = sessions_dir / name
                _remove_sidecars(target)
                _atomic_copy(journal / "checked" / name, target)
            store.reopen()
            repaired = store.repair_session_paths(sessions_dir)
            # Никогда не подставляем старый локальный ключ к строке из чужой копии.
            with store.tx() as conn:
                for row in conn.execute("SELECT name, session_file FROM sessions").fetchall():
                    name = row["name"]
                    if (not name or len(name) > 128 or name in {".", ".."}
                            or any(c in name for c in ("/", "\\", "\x00"))):
                        raise ValueError("В копии недопустимое имя аккаунта")
                    file_name = Path(row["session_file"] or f"{name}.session").name
                    if file_name not in names:
                        conn.execute("UPDATE sessions SET status='needs_login', last_error=? WHERE name=?",
                                     ("В копии отсутствует файл сессии", name))
            # Задачи из архива не должны самопроизвольно обращаться к Telegram.
            with store.tx() as conn:
                conn.execute("UPDATE gift_crawl_runs SET status='paused' WHERE status='running'")
                conn.execute("UPDATE nft_chain_runs SET status='paused' WHERE status='running'")
                conn.execute("UPDATE gift_heavy_tasks SET status='error', last_error=? WHERE status='running'",
                             ("После восстановления копии запустите задачу заново",))
            counts = _counts(store)
            checkpoint = store.query("PRAGMA wal_checkpoint(FULL)")[0]
            if checkpoint["busy"]:
                raise RuntimeError("Не удалось сохранить восстановленную базу")
            _sync_file(store.path)
            _sync_dir(store.path.parent)
            (journal / "committed").write_text("ok", encoding="utf-8")
            _sync_file(journal / "committed")
            _sync_dir(journal)
        except BaseException:
            # Даже если fsync маркера не удался, выбираем откат, не частичное применение.
            (journal / "committed").unlink(missing_ok=True)
            try:
                store.close()
            except sqlite3.Error:
                pass
            recover_pending_restore(store.path, sessions_dir)
            store.reopen()
            raise
        recover_pending_restore(store.path, sessions_dir)
        return {"sessions": len(files), "repaired_paths": repaired, "counts": counts,
                "created_at": manifest.get("created_at"), "source": "upload"}


__all__ = [
    "create_backup",
    "restore_backup",
    "list_backups",
    "latest_backup",
]


def recover_from_latest(home: Path, db_path: Path) -> str | None:
    """Проверяет копии до замены повреждённой БД; существующие сессии не трогает."""
    directory = Path(home) / "backups"
    if not directory.is_dir():
        return None
    archives = sorted(directory.glob("healdragg-*.zip"), key=lambda path: path.stat().st_mtime, reverse=True)
    for archive in archives:
        try:
            with tempfile.TemporaryDirectory(prefix=".recover-", dir=db_path.parent) as temp:
                staged = Path(temp) / "healdragg.db"
                with zipfile.ZipFile(archive) as bundle:
                    _validate(bundle)
                    with bundle.open("healdragg.db") as source, staged.open("wb") as target:
                        shutil.copyfileobj(source, target)
                _validate_database(staged)
                suffix = f".broken-{int(time())}-{uuid4().hex[:8]}"
                # Копии повреждённых файлов остаются на месте даже после восстановления.
                for sidecar in ("", "-wal", "-shm"):
                    original = Path(f"{db_path}{sidecar}")
                    if original.exists():
                        _atomic_copy(original, Path(f"{db_path}{suffix}{sidecar}"))
                _remove_sidecars(db_path)
                _atomic_copy(staged, db_path)
            log.warning("база восстановлена из копии %s; сессии не заменялись", archive.name)
            return archive.name
        except (zipfile.BadZipFile, ValueError, OSError, sqlite3.Error):
            log.exception("копия %s не подошла", archive.name)
    return None
