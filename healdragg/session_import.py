"""Импорт готовых Telegram-сессий: ``.session``, ``.json`` (StringSession), tdata.

Пользователю не нужно заново входить по коду: он выбирает файл (или архив
tdata), а приложение кладёт сессию в общий каталог и регистрирует аккаунт в
базе. Всё проверяется до записи: пустой/битый файл не попадёт в список
аккаунтов, а повторная загрузка того же файла не создаст дубль.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
import shutil
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from telethon.crypto import AuthKey
from telethon.sessions import SQLiteSession, StringSession

from .db import Store
from .tdata import convert_tdata, looks_like_tdata

log = logging.getLogger("healdragg.sessions")

MAX_UPLOAD = 200 * 1024 * 1024
_SLUG = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_name(raw: str, fallback: str = "session") -> str:
    stem = Path(str(raw or "")).stem
    cleaned = _SLUG.sub("-", stem).strip(".-_") or fallback
    return cleaned[:40]


def _read_session(path: Path) -> dict[str, Any] | None:
    """Читает готовый .session (Telethon/Pyrogram) без подключения к сети."""
    try:
        session = SQLiteSession(str(path))
    except Exception as exc:  # noqa: BLE001 — не наш формат
        log.info("не удалось открыть сессию %s: %s", path, exc)
        return None
    try:
        key = session.auth_key
        if not key or not getattr(key, "key", None):
            return None
        return {
            "dc_id": session.dc_id,
            "user_id": getattr(session, "user_id", None),
            "auth_key": bytes(key.key),
        }
    finally:
        session.close()


def session_info(path: Path) -> dict[str, Any] | None:
    """Публичная проверка файла сессии (для тестов и API)."""
    return _read_session(path)


def _store_session(store: Store, name: str, target: Path, info: dict[str, Any], *,
                   purpose: str = "scan", **extra: Any) -> dict[str, Any]:
    store.add_session(
        name,
        str(target),
        tg_user_id=info.get("user_id") if isinstance(info.get("user_id"), int) else None,
        status="active",
        purpose=purpose,
    )
    return {
        "name": name,
        "file": str(target),
        "dc_id": info.get("dc_id"),
        "tg_user_id": info.get("user_id"),
        "size": target.stat().st_size,
        **extra,
    }


def _unique_path(directory: Path, name: str, store: Store) -> tuple[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{name}.session"
    counter = 2
    while candidate.exists() or store.get_session(candidate.stem) is not None:
        candidate = directory / f"{name}-{counter}.session"
        counter += 1
    return candidate.stem, candidate


def _import_single(
    filename: str,
    payload: bytes,
    store: Store,
    sessions_dir: Path,
    *, purpose: str = "scan",
) -> dict[str, Any]:
    """Один файл: .session или .json. Возвращает описание результата."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".json":
        return _import_json(filename, payload, store, sessions_dir, purpose=purpose)
    if suffix not in {"", ".session", ".db"} and not suffix.startswith(".sess"):
        raise ValueError("поддерживаются .session, .json и tdata.zip")
    base = _safe_name(filename, "session")
    name, target = _unique_path(sessions_dir, base, store)
    target.write_bytes(payload)
    info = _read_session(target)
    if info is None:
        target.unlink(missing_ok=True)
        raise ValueError("файл не похож на Telethon-сессию: нет ключа авторизации")
    return _store_session(store, name, target, info, purpose=purpose, source=suffix.lstrip(".") or "session")


def _import_json(
    filename: str,
    payload: bytes,
    store: Store,
    sessions_dir: Path,
    *, purpose: str = "scan",
) -> dict[str, Any]:
    raw = payload.decode("utf-8", "replace").strip()
    session: StringSession | None = None
    try:
        session = StringSession(raw)
    except Exception:  # noqa: BLE001 — попробуем как обычный JSON
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("это не StringSession и не JSON") from exc
        key_raw = str(data.get("auth_key") or "")
        try:
            key = bytes.fromhex(key_raw)
        except ValueError as exc:
            raise ValueError("в JSON нет корректного auth_key") from exc
        session = StringSession()
        session.set_dc(
            int(data.get("dc_id") or 2),
            str(data.get("server_address") or "149.154.167.51"),
            int(data.get("port") or 443),
        )
        session.auth_key = AuthKey(key)
    key = session.auth_key
    if not key or not getattr(key, "key", None):
        raise ValueError("в StringSession нет ключа авторизации")
    name, target = _unique_path(sessions_dir, _safe_name(filename, "session"), store)
    sqlite = SQLiteSession(str(target))
    sqlite.set_dc(
        int(session.dc_id or 2),
        str(getattr(session, "server_address", None) or "149.154.167.51"),
        int(getattr(session, "port", 443) or 443),
    )
    sqlite.auth_key = AuthKey(bytes(key.key))
    sqlite.save()
    sqlite.close()
    info = _read_session(target) or {"dc_id": session.dc_id, "user_id": None}
    return _store_session(store, name, target, info, purpose=purpose, source="json")


def import_upload(
    filename: str,
    payload: bytes,
    store: Store,
    sessions_dir: Path,
    *, purpose: str = "scan",
) -> dict[str, Any]:
    """Разбирает загруженный файл и регистрирует все найденные аккаунты."""
    if purpose not in {"scan", "offers"}:
        raise ValueError("Неизвестное назначение сессии")
    if not payload:
        raise ValueError("пустой файл")
    if len(payload) > MAX_UPLOAD:
        raise ValueError("файл больше 200 МБ")
    suffix = Path(filename).suffix.lower()

    if suffix == ".zip" or payload[:2] == b"PK":
        if looks_like_tdata(payload):
            name, target = _unique_path(sessions_dir, _safe_name(filename, "tdata"), store)
            try:
                info = convert_tdata(payload, target)
            except ValueError:
                raise
            except Exception as exc:  # noqa: BLE001 — понятная ошибка вместо 500
                target.unlink(missing_ok=True)
                log.exception("конвертация tdata не удалась")
                raise ValueError(f"tdata не конвертировался: {str(exc)[:200]}") from exc
            entry = _store_session(
                store,
                name,
                target,
                {"dc_id": info.get("dc_id"), "user_id": info.get("user_id")},
                purpose=purpose,
                source="tdata",
                accounts=info.get("accounts"),
            )
            return {"imported": [entry], "skipped": [], "errors": []}
        return _import_zip(filename, payload, store, sessions_dir, purpose=purpose)

    entry = _import_single(filename, payload, store, sessions_dir, purpose=purpose)
    return {"imported": [entry], "skipped": [], "errors": []}


def _import_zip(
    filename: str,
    payload: bytes,
    store: Store,
    sessions_dir: Path,
    *, purpose: str = "scan",
) -> dict[str, Any]:
    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        entries = archive.infolist()
        if len(entries) > 5000 or len({entry.filename for entry in entries}) != len(entries):
            raise ValueError("Слишком много файлов или повторяющиеся имена в архиве")
        if sum(entry.file_size for entry in entries) > 400 * 1024 * 1024:
            raise ValueError("Содержимое архива слишком велико")
        candidates = [
            name
            for name in archive.namelist()
            if name.lower().endswith((".session", ".json")) and not name.endswith("/")
        ]
        if not candidates:
            raise ValueError(
                "в архиве нет .session/.json; для tdata заархивируйте папку с key_datas"
            )
        for name in candidates[:20]:
            try:
                if archive.getinfo(name).file_size > 100 * 1024 * 1024:
                    raise ValueError("Файл сессии больше 100 МБ")
                data = archive.read(name)
                result = _import_single(Path(name).name, data, store, sessions_dir, purpose=purpose)
                imported.append(result)
            except ValueError as exc:
                errors.append({"file": name, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                log.exception("импорт %s", name)
                errors.append({"file": name, "error": str(exc)[:200]})
    return {"imported": imported, "skipped": skipped, "errors": errors}


async def import_upload_async(
    filename: str,
    payload: bytes,
    store: Store,
    sessions_dir: Path,
) -> dict[str, Any]:
    """tdata конвертируется в отдельном потоке: не блокируем uvicorn."""
    return await asyncio.to_thread(import_upload, filename, payload, store, sessions_dir)


def decode_payload(content_b64: str) -> bytes:
    try:
        return base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("файл повреждён: base64 не читается") from exc


def cleanup(sessions_dir: Path) -> None:
    shutil.rmtree(sessions_dir / "__pycache__", ignore_errors=True)


__all__ = [
    "import_upload",
    "import_upload_async",
    "decode_payload",
    "session_info",
    "cleanup",
]
