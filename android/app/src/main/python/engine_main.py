"""Встроенный движок Healdragg для Android (Chaquopy).

Запускает ту же самую панель, что и ``healdragg api``, только процесс живёт
внутри APK: uvicorn крутится в фоновом потоке, Termux и pip на телефоне больше
не нужны.

Модуль вызывается из Kotlin (``net.syslikoff.healdragg.EngineService``):

    engine_main.start(home, api_id, api_hash, token, port) -> "started"
    engine_main.status() -> {"running": bool, ...}
    engine_main.stop() -> None
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any

HOST = "127.0.0.1"
LOG_FILE = "engine.log"

_lock = threading.Lock()
_thread: threading.Thread | None = None
_server: Any = None
_started_at = 0.0
_error = ""
_home: Path | None = None
_port = 8080

log = logging.getLogger("healdragg.android")


def _prepare_logging(home: Path) -> None:
    """Лог в файл: его видно в приложении, даже когда Termux нет."""
    log_dir = home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    target = log_dir / LOG_FILE
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.FileHandler):
            root.removeHandler(handler)
            handler.close()
    file_handler = logging.FileHandler(target, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(file_handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)


def _apply_env(home: Path, api_id: str, api_hash: str, token: str, port: int) -> None:
    os.environ.update(
        {
            "HEALDRAGG_HOME": str(home),
            "HEALDRAGG_ENV": str(home / ".env"),
            "API_ID": api_id,
            "API_HASH": api_hash,
            "API_TOKEN": token,
            "API_HOST": HOST,
            "API_PORT": str(port),
            "AUTOPILOT_MINUTES": "15",
            "AUTO_ACTIVITY_ON_SCAN": "true",
            "ALERT_PUSH": "true",
            "TZ": "Europe/Moscow",
        }
    )
    # .env внутри приложения: движок дописывает туда токен и настройки
    try:
        env_file = home / ".env"
        if not env_file.is_file():
            env_file.write_text(
                "\n".join(
                    [
                        f"API_ID={api_id}",
                        f"API_HASH={api_hash}",
                        f"API_TOKEN={token}",
                        "API_HOST=127.0.0.1",
                        f"API_PORT={port}",
                        "AUTOPILOT_MINUTES=15",
                        "AUTO_ACTIVITY_ON_SCAN=true",
                        "ALERT_PUSH=true",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
    except OSError:
        log.debug("не удалось записать .env", exc_info=True)


def start(
    home: str,
    api_id: str,
    api_hash: str,
    token: str,
    port: int = 8080,
) -> str:
    """Поднять панель в фоне. Повторный вызов перезапускает движок."""
    global _thread, _started_at, _error, _home, _port

    with _lock:
        if _thread is not None and _thread.is_alive():
            return "already"

        _home = Path(home)
        _home.mkdir(parents=True, exist_ok=True)
        _prepare_logging(_home)
        _apply_env(_home, api_id or "", api_hash or "", token or "", int(port or 8080))
        _port = int(port or 8080)
        _error = ""
        _started_at = time.time()

        _thread = threading.Thread(target=_serve, name="healdragg-engine", daemon=True)
        _thread.start()
        log.info("движок стартует на %s:%s (home=%s)", HOST, _port, _home)
        return "started"


def _serve() -> None:
    global _server, _error

    try:
        import uvicorn

        from healdragg.api import create_app
        from healdragg.config import load_settings

        settings = load_settings()
        app = create_app(settings)
        config = uvicorn.Config(
            app,
            host=HOST,
            port=_port,
            log_level="info",
            access_log=False,
            log_config=None,
        )
        _server = uvicorn.Server(config)
        asyncio.run(_serve_async(_server))
        log.info("движок остановлен")
    except BaseException as exc:  # noqa: BLE001 — падение должно быть видно в приложении
        _error = f"{exc.__class__.__name__}: {exc}"
        log.error("движок упал: %s\n%s", _error, traceback.format_exc())
    finally:
        _server = None


async def _serve_async(server: Any) -> None:
    await server.serve()


def stop() -> None:
    """Попросить сервер завершиться и дождаться потока."""
    global _thread

    with _lock:
        server = _server
        thread = _thread
    if server is not None:
        server.should_exit = True
    if thread is not None and thread.is_alive():
        thread.join(timeout=10)
    if thread is None or not thread.is_alive():
        with _lock:
            _thread = None


def status() -> dict[str, Any]:
    """Состояние для Kotlin: работает ли движок и что с ним не так."""
    alive = bool(_thread is not None and _thread.is_alive())
    return {
        "running": alive,
        "host": HOST,
        "port": _port,
        "uptime": round(time.time() - _started_at, 1) if _started_at else 0.0,
        "error": _error,
        "log": str(_home / "logs" / LOG_FILE) if _home else "",
    }


def log_tail(lines: int = 200) -> str:
    """Последние строки лога — для кнопки «Лог» в приложении."""
    if not _home:
        return ""
    path = _home / "logs" / LOG_FILE
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    rows = text.splitlines()
    return "\n".join(rows[-max(1, int(lines or 200)) :])
