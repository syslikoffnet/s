"""Импорт tdata (Telegram Desktop) в обычную Telethon-сессию.

Внутри лежит чистый Python-порт opentele (MIT, см. ``_vendor/README.md``) и
маленькие замены PyQt5/tgcrypto, поэтому конвертация работает прямо на телефоне,
без нативных пакетов и без интернета: читаем tdata, достаём auth key и пишем
``.session``, с которым дальше работает весь движок.
"""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

log = logging.getLogger("healdragg.tdata")

_VENDOR = Path(__file__).resolve().parent / "_vendor"
_MARKERS = {"key_datas", "settingss", "D877F783D5D3EF8C"}
MAX_ARCHIVE = 200 * 1024 * 1024


def vendor_ready() -> bool:
    """Есть ли в сборке конвертер (всегда да, но проверка не помешает)."""
    try:
        _ensure_path()
        import opentele.td  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        log.warning("конвертер tdata недоступен: %s", exc)
        return False
    return True


def _ensure_path() -> None:
    vendor = str(_VENDOR)
    if vendor not in sys.path:
        sys.path.insert(0, vendor)


def looks_like_tdata(zip_bytes: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            names = {Path(name).name for name in archive.namelist()}
    except zipfile.BadZipFile:
        return False
    return bool(names & _MARKERS)


def _safe_extract(zip_bytes: bytes, target: Path) -> None:
    if len(zip_bytes) > MAX_ARCHIVE:
        raise ValueError("архив слишком большой (максимум 200 МБ)")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for member in archive.infolist():
            name = member.filename
            parts = Path(name).parts
            if name.startswith("/") or ".." in parts:
                raise ValueError("в архиве недопустимый путь — импорт отменён")
            if member.is_dir():
                continue
            if member.file_size > MAX_ARCHIVE:
                raise ValueError("файл внутри архива слишком большой")
        archive.extractall(target)
    for path in target.rglob("*"):
        if path.is_symlink():
            path.unlink()


def _find_root(base: Path) -> Path | None:
    candidates = [base, *sorted(p for p in base.rglob("*") if p.is_dir())]
    for candidate in candidates:
        try:
            names = {item.name for item in candidate.iterdir()}
        except OSError:
            continue
        if names & _MARKERS:
            return candidate
    return None


async def _to_session(tdesk: Any, dest: Path) -> Any:
    from opentele.api import UseCurrentSession

    client = await tdesk.ToTelethon(session=str(dest), flag=UseCurrentSession)
    try:
        client.session.save()
    finally:
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:  # noqa: BLE001 — клиент мог не подключаться вовсе
            pass
    return client


def _explain(exc: BaseException) -> str:
    """Понятный текст вместо класса opentele (его ошибки наследуют BaseException)."""
    name = exc.__class__.__name__
    text = str(exc) or name
    if name in {"TDataInvalidMagic", "TDataInvalidCheckSum", "TFileNotFound"}:
        return "tdata повреждён или скопирован не целиком — заархивируйте папку tdata полностью"
    if name == "TDataBadDecryptKey":
        return "tdata зашифрован локальным паролем — снимите пароль в Telegram Desktop"
    if name in {"TDesktopHasNoAccount", "TDesktopNotLoaded"}:
        return "в tdata не нашлось аккаунтов"
    if name == "TDesktopUnauthorized":
        return "tdata не авторизован — войдите в Telegram Desktop"
    return text[:300]


def convert_tdata(zip_bytes: bytes, dest: Path) -> dict[str, Any]:
    """Распаковывает tdata.zip и пишет сессию в ``dest``. Синхронно, для потока."""
    if not zip_bytes:
        raise ValueError("пустой архив tdata")
    if not vendor_ready():
        raise ValueError(
            "в этой сборке нет конвертера tdata — загрузите .session или .json"
        )
    work = Path(tempfile.mkdtemp(prefix="tdata-"))
    try:
        _safe_extract(zip_bytes, work)
        root = _find_root(work)
        if root is None:
            raise ValueError(
                "это не tdata: внутри архива нет key_datas/settingss. "
                "Заархивируйте папку tdata целиком (внутри zip должен быть key_datas)"
            )
        from opentele.api import API
        from opentele.td import TDesktop

        try:
            tdesk = TDesktop(str(root), api=API.TelegramDesktop)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException as exc:  # opentele кидает BaseException
            raise ValueError(_explain(exc)) from exc
        if not tdesk.isLoaded():
            raise ValueError("tdata не открылся: возможно, включён локальный пароль")
        if tdesk.accountsCount <= 0:
            raise ValueError("в tdata нет аккаунтов")
        account = tdesk.mainAccount
        user_id = getattr(account, "UserId", None) or getattr(account, "UserID", None)
        dc_id = getattr(account, "MainDcId", None)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            asyncio.run(_to_session(tdesk, dest))
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException as exc:
            raise ValueError(_explain(exc)) from exc
        if not dest.is_file() or dest.stat().st_size == 0:
            raise ValueError("конвертация tdata не дала сессию")
        return {
            "source": "tdata",
            "accounts": int(tdesk.accountsCount),
            "user_id": int(user_id) if isinstance(user_id, int) else None,
            "dc_id": int(dc_id) if isinstance(dc_id, int) else None,
            "session_file": str(dest),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


__all__ = ["convert_tdata", "looks_like_tdata", "vendor_ready"]
