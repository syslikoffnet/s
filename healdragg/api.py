"""REST API + веб-панель (её же открывает Android-приложение).

Токен берётся из ``API_TOKEN`` (генерируется при первом запуске).
Передавай его заголовком ``X-Api-Token`` или параметром ``?token=``.
"""

from __future__ import annotations

import asyncio
import csv
import hmac
import io
import logging
import re
import sqlite3
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from . import __version__
from .activity import collect_activity
from .backup import (
    MAX_ARCHIVE_BYTES,
    create_backup,
    list_backups,
    recover_from_latest,
    recover_pending_restore,
    restore_backup,
)
from .config import Settings
from .db import Store
from .gift_art import GiftArt
from .gifts import crawl_gifts, crawl_nft_chain, crawl_nft_profiles, refresh_gifts, resolve_unique_gift
from .graph import build_global_graph, build_graph
from .intel import guess
from .login import LoginError, LoginManager
from .manual import import_groups, parse_batch
from .money import read_display, write_display
from .profile import photo_base64, refresh_profile
from .scan import bootstrap, resolve_and_store_target, scan_target
from .session_import import decode_payload, import_upload
from .textfmt import (
    render_account_chats,
    render_catalog,
    render_profile,
    render_scan_progress,
    render_stats,
)
from .tg import SessionPool
from .util import Page, fmt_relative, human_size, now_ts

log = logging.getLogger("healdragg.api")
WEB_DIR = Path(__file__).parent / "web"


@dataclass
class Job:
    key: str
    kind: str
    task: asyncio.Task | None = None
    state: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    finished: bool = False
    error: str | None = None
    target_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        elapsed = time.time() - self.started_at
        return {
            "key": self.key,
            "kind": self.kind,
            "state": self.state,
            "started_at": int(self.started_at),
            "elapsed": round(elapsed, 1),
            "finished": self.finished,
            "error": self.error,
            "target_id": self.target_id,
            "progress": render_scan_progress(
                {
                    "total": self.state.get("total", 0),
                    "done": self.state.get("done", 0),
                    "found": self.state.get("found", 0),
                    "started_at": int(self.started_at),
                }
            )
            if not self.finished
            else None,
        }


class AppState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.ensure_dirs()
        recover_pending_restore(settings.db_path, settings.sessions_dir)
        try:
            self.store = Store(settings.db_path)
        except sqlite3.DatabaseError as exc:
            # Только подтверждённое повреждение, не нехватка места, прав или ошибки миграций.
            code = getattr(exc, "sqlite_errorcode", None)
            if code is None or (code & 0xFF) not in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                raise
            log.exception("база повреждена: %s", exc)
            restored = recover_from_latest(settings.db_path.parent, settings.db_path)
            if not restored:
                raise
            self.store = Store(settings.db_path)
        self.pool = SessionPool(settings, self.store)
        self.gift_art = GiftArt(settings.home, self.store, self.pool)
        self.login = LoginManager(settings, self.store)
        self.jobs: dict[str, Job] = {}
        self.live_at: dict[str, float] = {}
        # Не допускаем одновременных снимков профилей от разных задач подарков.
        self.gift_lock = asyncio.Lock()
        self.session_mutation_lock = asyncio.Lock()
        self.auto_backup_busy = False
        self.auto_backup_task: asyncio.Task | None = None
        self.autopilot_task: asyncio.Task | None = None
        self.request_count = 0
        self.restoring = False

    def running(self, key: str) -> bool:
        job = self.jobs.get(key)
        return bool(job and not job.finished)

    async def cancel(self, key: str) -> bool:
        job = self.jobs.get(key)
        if not job or job.finished or job.task is None:
            return False
        job.task.cancel()
        try:
            await job.task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            pass
        job.finished = True
        job.state = {**job.state, "phase": "отменено"}
        return True

    async def close(self) -> None:
        tasks = [job.task for job in self.jobs.values() if job.task and not job.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.login.close()
        await self.pool.close()
        self.store.close()


def _target_payload(store: Store, target: dict[str, Any]) -> dict[str, Any]:
    stats = store.target_stats(int(target["id"]))
    return {
        **{k: v for k, v in target.items() if k not in {"access_hash", "photo_path"}},
        "photo": {
            "present": bool(target.get("photo_path")),
            "updated_at": int(target.get("photo_updated_at") or 0),
        },
        "intel": {
            "gender": target.get("gender"),
            "gender_confidence": target.get("gender_confidence"),
            "country": target.get("country"),
            "country_name": target.get("country_name"),
            "country_confidence": target.get("country_confidence"),
            "source": target.get("intel_source"),
            "updated_at": target.get("intel_updated_at"),
        },
        "gifts": {
            "count": int(target.get("gifts_count") or 0),
            "updated_at": target.get("gifts_updated_at"),
        },
        "stats": stats,
        "activity": store.activity_stats(int(target["id"])),
        "alerts_unread": store.count_alerts(),
        "profile_text": render_profile(target, stats),
        "last_scan": fmt_relative(target.get("last_scan_at")),
    }


def _groups_payload(
    store: Store,
    target: dict[str, Any],
    *,
    page: int,
    size: int,
    order: str = "last",
) -> dict[str, Any]:
    total = store.count_target_groups(int(target["id"]))
    pg = Page.build(total, page, size)
    rows = store.list_target_groups(int(target["id"]), page=pg.page, size=size, order=order)
    text = render_account_chats(
        rows,
        target_id=int(target["tg_user_id"]),
        page=pg,
        title_limit=15,
        escape_html=False,
    )
    return {
        "total": total,
        "page": pg.page,
        "pages": pg.pages,
        "size": size,
        "rows": [
            {
                "title": r.get("title"),
                "tg_id": r.get("tg_id"),
                "username": r.get("username"),
                "link": r.get("link"),
                "kind": r.get("kind"),
                "is_public": bool(r.get("is_public")),
                "status": r.get("status"),
                "members_count": r.get("members_count"),
                "messages_total": r.get("messages_total"),
                "last_message_at": r.get("last_message_at"),
                "source": r.get("source"),
            }
            for r in rows
        ],
        "text": text,
    }


def _q_int(request: Request, name: str, default: int, lo: int, hi: int) -> int:
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise HTTPException(400, f"{name}: нужно число") from exc
    return max(lo, min(hi, value))


def _q_bool(request: Request, name: str, default: bool = False) -> bool:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _q_chain(request: Request) -> str | None:
    """``wallet`` — NFT на TON-кошельке, ``telegram`` — внутри аккаунта TG."""
    raw = (request.query_params.get("chain") or "").strip().lower()
    if raw in {"wallet", "chain", "onchain", "on-chain", "ton"}:
        return "wallet"
    if raw in {"telegram", "tg", "hosted", "account"}:
        return "telegram"
    return None


def _q_flag(request: Request, name: str) -> bool:
    """Флаги в query: ``1``, ``true``, ``yes``, ``on`` — включено."""
    raw = (request.query_params.get(name) or "").strip().lower()
    return raw in {"1", "true", "yes", "on", "да"}


def _q_collection(request: Request) -> str | None:
    """Коллекция NFT: принимаем и slug (``PlushPepe``), и ``PlushPepe-1234``."""
    raw = (request.query_params.get("collection") or "").strip()
    if not raw:
        return None
    return raw.split("-", 1)[0]


async def _body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "Некорректный JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "Ожидался JSON-объект")
    return data


def _route(fn: Any) -> Any:
    """JSON-ответ по умолчанию: dict/list → JSONResponse, Response — как есть.

    Любое исключение превращается в понятный JSON: приложение показывает
    текст ошибки, а не «internal error» без деталей.
    """

    @wraps(fn)
    async def wrapper(request: Request) -> Any:
        try:
            result = await fn(request)
        except HTTPException:
            raise
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "ok": False}, status_code=400)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — последний рубеж
            log.exception("api %s %s", request.method, request.url.path)
            message = str(exc).strip() or exc.__class__.__name__
            return JSONResponse(
                {
                    "error": f"Внутренняя ошибка: {message[:300]}",
                    "kind": exc.__class__.__name__,
                    "path": request.url.path,
                    "ok": False,
                },
                status_code=500,
            )
        if isinstance(result, Response):
            return result
        return JSONResponse(result)

    return wrapper


async def _safe_io(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Не закрываем базу, пока отменённый to_thread ещё работает с ней."""
    worker = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await worker
        raise


class RestoreGate:
    """Не допускает параллельных API-запросов при замене базы и сессий."""

    def __init__(self, app: Any, state: AppState) -> None:
        self.app, self.state = app, state

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        restoring = scope["path"] == "/api/backup/restore" and scope["method"] == "POST"
        # На одном event loop без await эти проверки и изменение счётчика атомарны.
        if self.state.restoring or (restoring and self.state.request_count):
            response = JSONResponse({"error": "Дождитесь завершения других запросов и повторите"}, status_code=409)
            await response(scope, receive, send)
            return
        if restoring:
            self.state.restoring = True
        self.state.request_count += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.state.request_count -= 1
            if restoring:
                self.state.restoring = False


def _auto_backup(store: Store, sessions_dir: Path | None = None) -> dict[str, Any]:
    """Копия при старте движка: без неё «ничего не теряется» — просто слова."""
    return create_backup(store, keep=5, label="auto", sessions_dir=sessions_dir)


def create_app(settings: Settings) -> Starlette:
    token = settings.ensure_api_token()
    state = AppState(settings)

    @asynccontextmanager
    async def lifespan(_: Starlette):
        repaired = state.store.repair_session_paths(settings.sessions_dir)
        if repaired:
            log.info("починил пути к сессиям: %s", repaired)
        for run in state.store.gift_crawls(("running",)):
            launch_gift_crawl(int(run["id"]))
        for task in state.store.running_heavy_tasks():
            launch_heavy_task(task)
        chain_run = state.store.nft_chain_run()
        if chain_run and chain_run["status"] == "running":
            launch_nft_chain_run(int(chain_run["id"]))
        if settings.auto_backup:
            async def _backup_once() -> None:
                await asyncio.sleep(8)
                state.auto_backup_busy = True
                try:
                    created = await _safe_io(_auto_backup, state.store, settings.sessions_dir)
                    log.info("авто-копия: %s (%s)", created["name"], created["size"])
                except Exception:  # noqa: BLE001 — копия не должна ронять сервер
                    log.exception("авто-копия не удалась")
                finally:
                    state.auto_backup_busy = False

            state.auto_backup_task = asyncio.create_task(_backup_once(), name="auto-backup")
        if settings.autopilot_minutes > 0:
            from .autopilot import loop as autopilot_loop

            state.autopilot_task = asyncio.create_task(
                autopilot_loop(state.pool, state.store, settings), name="autopilot"
            )
            log.info("автопилот: каждые %s мин", settings.autopilot_minutes)
        try:
            yield
        finally:
            if state.auto_backup_task:
                state.auto_backup_task.cancel()
                try:
                    await state.auto_backup_task
                except asyncio.CancelledError:
                    pass
            if state.autopilot_task:
                state.autopilot_task.cancel()
                try:
                    await state.autopilot_task
                except asyncio.CancelledError:
                    pass
            await state.close()

    def guard(request: Request) -> None:
        supplied = (
            request.headers.get("x-api-token")
            or request.query_params.get("token")
            or request.cookies.get("hd_token")
            or ""
        )
        if not supplied or not hmac.compare_digest(supplied, token):
            raise HTTPException(401, "Неверный токен")

    # ── служебное ───────────────────────────────────────────────────────────
    async def health(_: Request) -> dict[str, Any]:
        return {"ok": True, "version": __version__, "time": now_ts()}

    async def stats(request: Request) -> dict[str, Any]:
        guard(request)
        data = state.store.stats()
        return {
            **data,
            "db_human": human_size(data.get("db_bytes") or 0),
            "text": render_stats(data),
            "counts": state.store.table_counts(),
        }

    async def sessions(request: Request) -> list[dict[str, Any]]:
        guard(request)
        return [
            {
                "name": s["name"],
                "username": s.get("username"),
                "tg_user_id": s.get("tg_user_id"),
                "status": s.get("status"),
                "groups_count": s.get("groups_count"),
                "last_used_at": s.get("last_used_at"),
            }
            for s in state.store.list_sessions()
        ]

    async def session_import(request: Request) -> dict[str, Any]:
        """Импорт готового аккаунта: .session, StringSession .json или tdata.zip."""
        guard(request)
        body = await _body(request)
        filename = str(body.get("filename") or "").strip() or "session.session"
        content = body.get("content_b64")
        if not isinstance(content, str) or not content:
            raise HTTPException(400, "нужен файл: filename + content_b64")
        try:
            payload = decode_payload(content)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        async with state.session_mutation_lock:
            try:
                result = await _safe_io(import_upload, filename, payload, state.store, settings.sessions_dir)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            if result.get("imported"):
                try:
                    await _safe_io(create_backup, state.store, keep=8, label="import", sessions_dir=settings.sessions_dir)
                except Exception:  # noqa: BLE001 — копия не должна ломать импорт
                    log.exception("бэкап после импорта не удался")
            return result

    async def backup_list(request: Request) -> dict[str, Any]:
        guard(request)
        rows = list_backups(state.store)
        return {
            "backups": rows,
            "latest": rows[0] if rows else None,
            "counts": {
                **state.store.table_counts(),
                "nft": state.store.nft_summary()["total"],
            },
            "db_bytes": state.store.path.stat().st_size if state.store.path.exists() else 0,
        }

    async def graph_global(request: Request) -> dict[str, Any]:
        """Общая карта OSINT: цели ↔ чаты ↔ собеседники одним графом."""
        guard(request)
        return build_global_graph(
            state.store,
            max_targets=_q_int(request, "targets", 60, 1, 500),
            max_groups=_q_int(request, "chats", 120, 1, 2000),
            max_peers=_q_int(request, "peers", 200, 1, 2000),
        )

    async def display_get(request: Request) -> dict[str, Any]:
        """Валюта приложения и курсы — карточки показывают цены в ней."""
        guard(request)
        return read_display(state.store)

    async def display_post(request: Request) -> dict[str, Any]:
        guard(request)
        body = await _body(request)
        return write_display(state.store, body)

    async def backup_create(request: Request) -> dict[str, Any]:
        guard(request)
        return await _safe_io(create_backup, state.store, keep=10, label="manual", sessions_dir=settings.sessions_dir)

    async def backup_download(request: Request) -> Response:
        guard(request)
        name = (request.query_params.get("name") or "").strip()
        if _q_bool(request, "fresh"):
            created = await _safe_io(create_backup, state.store, keep=10, label="manual", sessions_dir=settings.sessions_dir)
            return FileResponse(created["path"], filename=created["name"], media_type="application/zip")
        rows = list_backups(state.store)
        if not rows:
            created = await _safe_io(create_backup, state.store, keep=10, label="manual", sessions_dir=settings.sessions_dir)
            return FileResponse(created["path"], filename=created["name"])
        chosen = next((row for row in rows if row["name"] == name), rows[0] if not name else None)
        if chosen is None:
            raise HTTPException(404, "копия не найдена")
        return FileResponse(chosen["path"], filename=chosen["name"], media_type="application/zip")

    async def backup_restore(request: Request) -> dict[str, Any]:
        """Восстанавливает только при остановленных задачах и закрытых сессиях."""
        guard(request)
        if any(not job.finished for job in state.jobs.values()):
            raise HTTPException(409, "Остановите активные задачи перед восстановлением копии")
        if state.auto_backup_busy:
            raise HTTPException(409, "Дождитесь завершения автоматической копии")
        if state.auto_backup_task and not state.auto_backup_task.done():
            state.auto_backup_task.cancel()
            try:
                await state.auto_backup_task
            except asyncio.CancelledError:
                pass
            state.auto_backup_task = None
        # Сетевой ввод пишется в файл, а не держит сотни мегабайт в памяти.
        with tempfile.TemporaryFile(dir=settings.home) as payload:
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise HTTPException(413, "архив больше 400 МБ")
                await _safe_io(payload.write, chunk)
            if not total:
                raise HTTPException(400, "пустой файл копии")
            payload.seek(0)
            if state.autopilot_task:
                state.autopilot_task.cancel()
                try:
                    await state.autopilot_task
                except asyncio.CancelledError:
                    pass
                state.autopilot_task = None
            try:
                await state.login.close_for_restore()
                await state.pool.close_for_restore()
                result = await _safe_io(restore_backup, payload, state.store, settings.sessions_dir)
                state.jobs.clear()
                log.warning("восстановление из копии: %s", result)
                return result
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            finally:
                if settings.autopilot_minutes > 0:
                    from .autopilot import loop as autopilot_loop
                    state.autopilot_task = asyncio.create_task(
                        autopilot_loop(state.pool, state.store, settings), name="autopilot"
                    )

    async def session_start(request: Request) -> dict[str, Any]:
        guard(request)
        body = await _body(request)
        phone = str(body.get("phone") or "")
        name = str(body.get("name") or "main")
        try:
            async with state.session_mutation_lock:
                return await state.login.start(phone, name)
        except LoginError as exc:
            raise HTTPException(400, str(exc)) from exc

    async def session_code(request: Request) -> dict[str, Any]:
        guard(request)
        body = await _body(request)
        try:
            return await state.login.submit_code(
                str(body.get("id") or ""), str(body.get("code") or "")
            )
        except LoginError as exc:
            raise HTTPException(400, str(exc)) from exc

    async def session_password(request: Request) -> dict[str, Any]:
        guard(request)
        body = await _body(request)
        try:
            return await state.login.submit_password(
                str(body.get("id") or ""), str(body.get("password") or "")
            )
        except LoginError as exc:
            raise HTTPException(400, str(exc)) from exc

    async def session_cancel(request: Request) -> dict[str, Any]:
        guard(request)
        await state.login.cancel(str(request.path_params.get("flow_id") or ""))
        return {"ok": True}

    async def delete_session(request: Request) -> dict[str, Any]:
        guard(request)
        name = str(request.path_params.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name обязателен")
        if name not in state.store.session_names(only_active=False):
            raise HTTPException(404, "Сессии нет")
        for config in state.store.running_heavy_tasks():
            if config["session"] != name:
                continue
            owner = int(config["owner_tg_id"])
            key = f"gifts:heavy:{owner}"
            error = "Выбранный аккаунт удалён. Выберите другой и запустите задачу заново."
            state.store.set_heavy_task_status(owner, "error", error=error)
            await state.cancel(key)
            job = state.jobs.get(key)
            if job:
                job.error = error
                job.state = {**job.state, "phase": "аккаунт удалён"}
        await state.pool.disconnect(name)
        row = state.store.get_session(name) or {}
        session_file = settings.sessions_dir / Path(row.get("session_file") or settings.session_file(name)).name
        shared = any(other["name"] != name and Path(other.get("session_file") or "").name == session_file.name
                     for other in state.store.list_sessions())
        state.store.remove_session(name)
        try:
            if not shared:
                session_file.unlink(missing_ok=True)
        except OSError:
            log.warning("не удалось удалить %s", session_file)
        state.store.log_event("info", f"сессия {name} удалена", "app")
        return {"ok": True, "name": name}

    # ── цели ────────────────────────────────────────────────────────────────
    async def targets(request: Request) -> list[dict[str, Any]]:
        guard(request)
        return [_target_payload(state.store, t) for t in state.store.list_targets(100)]

    async def targets_page(request: Request) -> dict[str, Any]:
        guard(request)
        query = (request.query_params.get("q") or "").strip()
        if len(query) > 128:
            raise HTTPException(400, "q: не более 128 символов")
        raw_cursor = request.query_params.get("before")
        if raw_cursor:
            try:
                before = int(raw_cursor)
                if before < 1 or before > 9_223_372_036_854_775_807:
                    raise ValueError
            except ValueError as exc:
                raise HTTPException(400, "before: неверный курсор") from exc
        else:
            before = None
        limit = _q_int(request, "limit", 50, 1, 100)
        rows = state.store.targets_page(before_id=before, query=query, limit=limit)
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "rows": [_target_payload(state.store, row) for row in rows],
            "next_before": int(rows[-1]["id"]) if has_more else None,
        }

    async def target_lookup(request: Request) -> dict[str, Any]:
        guard(request)
        query = (request.query_params.get("q") or "").strip()
        if not 1 <= len(query) <= 128:
            raise HTTPException(400, "q: от 1 до 128 символов")
        target = state.store.find_target(query)
        if target is None:
            raise HTTPException(404, "Цель не найдена")
        return _target_payload(state.store, target)

    async def add_target(request: Request) -> dict[str, Any]:
        guard(request)
        body = await _body(request)
        query = str(body.get("query") or "").strip()
        if not 1 <= len(query) <= 128:
            raise HTTPException(400, "query: от 1 до 128 символов")
        if not state.store.session_names():
            raise HTTPException(409, "Нет аккаунта-сканера — войди в Telegram в приложении")
        try:
            target = await resolve_and_store_target(state.pool, state.store, query)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Не удалось найти аккаунт: {exc}") from exc
        return _target_payload(state.store, target)

    async def search_people(request: Request) -> dict[str, Any]:
        guard(request)
        query = (request.query_params.get("q") or "").strip()
        if not 1 <= len(query) <= 128:
            raise HTTPException(400, "q: от 1 до 128 символов")
        raw_cursor = request.query_params.get("before")
        before = None
        if raw_cursor:
            try:
                before = int(raw_cursor)
                if not 1 <= before <= 9_223_372_036_854_775_807:
                    raise ValueError
            except ValueError as exc:
                raise HTTPException(400, "before: неверный курсор") from exc
        limit = _q_int(request, "limit", 25, 1, 50)
        rows = state.store.search_observed_people(query, before_id=before, limit=limit)
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {"rows": rows, "next_before": int(rows[-1]["tg_id"]) if has_more else None}

    async def open_profile(request: Request) -> dict[str, Any]:
        guard(request)
        body = await _body(request)
        query = str(body.get("query") or "").strip()
        if not 1 <= len(query) <= 128:
            raise HTTPException(400, "query: от 1 до 128 символов")
        known = state.store.find_target(query)
        if known:
            return _target_payload(state.store, known)
        # Открыть уже наблюдавшийся ID можно и без Telegram-сессии. Метаданные
        # могут устареть; открытие не включает автопилот и не делает сетевой запрос.
        if query.isascii() and query.isdigit() and int(query) <= 9_223_372_036_854_775_807:
            observed = state.store.search_observed_people(exact_id=int(query), limit=1, exclude_saved=False)
            if observed:
                person = observed[0]
                target = state.store.upsert_target(
                    int(person["tg_id"]), username=person.get("username"),
                    first_name=person.get("name") or None, watch_enabled=False,
                )
                return _target_payload(state.store, target)
        if not state.store.session_names():
            raise HTTPException(409, "Подключите Telegram, чтобы найти неизвестный профиль")
        try:
            target = await resolve_and_store_target(state.pool, state.store, query, watch_enabled=False)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Не удалось найти профиль: {exc}") from exc
        return _target_payload(state.store, target)

    async def get_target(request: Request) -> dict[str, Any]:
        guard(request)
        target = state.store.get_target(request.path_params["tg_id"])
        if not target:
            raise HTTPException(404, "Цель не найдена")
        return _target_payload(state.store, target)

    async def delete_target(request: Request) -> dict[str, Any]:
        guard(request)
        state.store.delete_target(request.path_params["tg_id"])
        return {"ok": True}

    async def profile_gifts(request: Request) -> dict[str, Any]:
        guard(request)
        tg_id = request.path_params["tg_id"]
        if not state.store.get_target(tg_id):
            raise HTTPException(404, "Профиль не найден")
        direction = request.query_params.get("direction", "received")
        if direction not in {"received", "sent"}:
            raise HTTPException(400, "direction: received или sent")
        limit = _q_int(request, "limit", 50, 1, 100)
        offset = _q_int(request, "offset", 0, 0, 1_000_000)
        raw_cursor = request.query_params.get("before")
        before = None
        if raw_cursor:
            try:
                before = int(raw_cursor)
                if not 1 <= before <= 9_223_372_036_854_775_807:
                    raise ValueError
            except ValueError as exc:
                raise HTTPException(400, "before: неверный курсор") from exc
        if before is not None and offset:
            raise HTTPException(400, "Используйте курсор или offset, но не оба сразу")
        rows = state.store.profile_gifts(tg_id, direction, limit=limit, offset=offset, before_id=before)
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {"rows": rows, "has_more": has_more,
                "next_before": int(rows[-1]["id"]) if has_more else None}

    async def search_gifts(request: Request) -> dict[str, Any]:
        guard(request)
        query = (request.query_params.get("q") or "").strip()
        if not 1 <= len(query) <= 128:
            raise HTTPException(400, "q: от 1 до 128 символов")
        return state.store.search_gift_records(query, limit=_q_int(request, "limit", 20, 1, 50))

    async def search_unique_gift(request: Request) -> dict[str, Any]:
        from telethon.errors import RPCError

        guard(request)
        body = await _body(request)
        slug = str(body.get("slug") or "").strip()
        if len(slug) > 128 or not re.fullmatch(r"[A-Za-z0-9_]+-[1-9][0-9]*", slug):
            raise HTTPException(400, "Укажите ссылку или slug NFT, например PlushPepe-123")
        if not state.store.session_names():
            raise HTTPException(409, "Нет активного аккаунта Telegram")
        if state.gift_lock.locked():
            raise HTTPException(409, "Сейчас идёт другой сбор подарков, повторите позже")
        try:
            async with state.gift_lock:
                row = await resolve_unique_gift(state.pool, state.store, slug)
        except RPCError as exc:
            if getattr(exc, "message", None) == "STARGIFT_SLUG_INVALID":
                raise HTTPException(404, "Telegram не нашёл этот NFT") from exc
            raise HTTPException(502, f"Telegram: {exc}") from exc
        except Exception as exc:
            raise HTTPException(502, f"Не удалось получить NFT: {exc}") from exc
        return {"item": row}

    async def target_groups(request: Request) -> dict[str, Any]:
        guard(request)
        target = state.store.get_target(request.path_params["tg_id"])
        if not target:
            raise HTTPException(404, "Цель не найдена")
        order = request.query_params.get("order") or "last"
        if order not in {"last", "members", "title", "found"}:
            order = "last"
        return _groups_payload(
            state.store,
            target,
            page=_q_int(request, "page", 1, 1, 10_000),
            size=_q_int(request, "size", 15, 1, 100),
            order=order,
        )

    # ── алерты ──────────────────────────────────────────────────────────────
    async def alerts(request: Request) -> dict[str, Any]:
        guard(request)
        return {
            "rows": state.store.list_alerts(
                limit=_q_int(request, "limit", 50, 1, 200),
                unread_only=_q_bool(request, "unread"),
            ),
            "unread": state.store.count_alerts(),
        }

    async def alerts_read(request: Request) -> dict[str, Any]:
        guard(request)
        body = await _body(request)
        ids = body.get("ids")
        if ids is not None and not (isinstance(ids, list) and all(isinstance(i, int) for i in ids)):
            raise HTTPException(400, "ids: список чисел")
        changed = state.store.mark_alerts_read(ids)
        return {"ok": True, "changed": changed, "unread": state.store.count_alerts()}

    # ── ссылки / граф / активность ──────────────────────────────────────────
    async def target_links(request: Request) -> dict[str, Any]:
        guard(request)
        target = state.store.get_target(request.path_params["tg_id"])
        if not target:
            raise HTTPException(404, "Цель не найдена")
        target_pk = int(target["id"])
        offset = _q_int(request, "offset", 0, 0, 1_000_000)
        limit = _q_int(request, "limit", 100, 1, 500)
        total = state.store.count_target_groups(target_pk)
        chunk = state.store.target_group_links_page(target_pk, offset=offset, limit=limit)
        return {
            "total": total,
            "offset": offset,
            "has_more": offset + len(chunk) < total,
            "rows": [
                {
                    "title": row.get("title"),
                    "link": row.get("link"),
                    "username": row.get("username"),
                    "tg_id": row.get("tg_id"),
                    "status": row.get("status"),
                }
                for row in chunk
            ],
        }

    async def target_graph(request: Request) -> dict[str, Any]:
        guard(request)
        target = state.store.get_target(request.path_params["tg_id"])
        if not target:
            raise HTTPException(404, "Цель не найдена")
        return build_graph(
            state.store,
            int(target["id"]),
            max_peers=_q_int(request, "max_peers", 200, 10, 500),
        )

    async def target_activity(request: Request) -> dict[str, Any]:
        guard(request)
        target = state.store.get_target(request.path_params["tg_id"])
        if not target:
            raise HTTPException(404, "Цель не найдена")
        target_pk = int(target["id"])
        return {
            "stats": state.store.activity_stats(target_pk),
            "rows": state.store.activity_feed(
                target_pk, limit=_q_int(request, "limit", 60, 1, 300)
            ),
            "edges": state.store.target_edges(target_pk, limit=50),
        }

    async def live_activity(request: Request) -> dict[str, Any]:
        """Режим наблюдения: тихо добирает свежие сообщения цели.

        Отдаёт текущую ленту сразу и запускает не более одного фонового
        сбора на цель, чтобы медленный Telegram не задерживал обновление UI.
        """
        guard(request)
        tg_id = request.path_params["tg_id"]
        target = state.store.get_target(tg_id)
        if not target:
            raise HTTPException(404, "Цель не найдена")
        target_pk = int(target["id"])

        def feed_payload(extra: dict[str, Any] | None = None) -> dict[str, Any]:
            payload = {
                "stats": state.store.activity_stats(target_pk),
                "rows": state.store.activity_feed(
                    target_pk, limit=_q_int(request, "limit", 120, 1, 300)
                ),
                "edges": state.store.target_edges(target_pk, limit=50),
            }
            if extra:
                payload.update(extra)
            return payload

        if not state.store.session_names():
            return feed_payload({"skipped": "нет аккаунта"})
        live_key = f"live:{tg_id}"
        if any(state.running(key) for key in ("boot", "import", f"act:{tg_id}", live_key)):
            return feed_payload({"skipped": "занято"})
        stamp = time.time()
        if stamp - state.live_at.get(str(tg_id), 0) < 3.5:
            return feed_payload({"skipped": "пауза"})
        state.live_at[str(tg_id)] = stamp
        job = Job(key=live_key, kind="live_activity", target_id=tg_id)
        state.jobs[live_key] = job

        async def runner() -> None:
            try:
                job.state = await collect_activity(
                    state.pool,
                    state.store,
                    settings,
                    target,
                    days=2,
                    per_chat=settings.activity_per_chat,
                    max_chats=settings.activity_max_chats,
                    incremental=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                job.error = str(exc)
                log.warning("live activity %s: %s", tg_id, exc)
            finally:
                job.finished = True

        job.task = asyncio.create_task(runner(), name=live_key)
        return feed_payload({"skipped": None})

    # ── фоновые задачи ──────────────────────────────────────────────────────
    async def collect_target_activity(request: Request) -> dict[str, Any]:
        guard(request)
        tg_id = request.path_params["tg_id"]
        target = state.store.get_target(tg_id)
        if not target:
            raise HTTPException(404, "Цель не найдена")
        if not state.store.session_names():
            raise HTTPException(409, "Нет аккаунта-сканера — войди в Telegram в приложении")
        key = f"act:{tg_id}"
        if state.running(key) or state.running(f"live:{tg_id}"):
            raise HTTPException(409, "Сбор активности уже идёт")
        job = Job(key=key, kind="activity", target_id=tg_id)
        job.state = {"phase": "старт", "done": 0, "total": 0, "found": 0}
        state.jobs[key] = job

        async def on_progress(progress: dict[str, Any]) -> None:
            job.state = progress

        async def runner() -> None:
            try:
                await collect_activity(
                    state.pool,
                    state.store,
                    settings,
                    target,
                    days=_q_int(request, "days", 30, 1, 365),
                    per_chat=_q_int(request, "per_chat", settings.activity_per_chat, 1, 200),
                    max_chats=_q_int(request, "max_chats", settings.activity_max_chats, 1, 500),
                    reporter=on_progress,
                )
                job.state = {**job.state, "phase": "готово"}
            except asyncio.CancelledError:
                job.state = {**job.state, "phase": "отменено"}
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("api activity failed")
                job.error = str(exc)
            finally:
                job.finished = True

        job.task = asyncio.create_task(runner(), name=key)
        return job.as_dict()

    async def refresh_target_profile(request: Request) -> dict[str, Any]:
        guard(request)
        tg_id = request.path_params["tg_id"]
        target = state.store.get_target(tg_id)
        if not target:
            raise HTTPException(404, "Цель не найдена")
        if not state.store.session_names():
            raise HTTPException(409, "Нет аккаунта-сканера — войди в Telegram в приложении")
        try:
            data = await refresh_profile(state.pool, state.store, settings, target)
        except Exception as exc:  # noqa: BLE001 — сеть Telegram
            log.exception("профиль цели не обновился")
            raise HTTPException(502, f"Telegram не ответил: {exc}") from exc
        return {**data, "target": _target_payload(state.store, state.store.get_target(tg_id) or target)}

    async def target_photo(request: Request) -> dict[str, Any]:
        guard(request)
        tg_id = request.path_params["tg_id"]
        target = state.store.get_target(tg_id)
        if not target:
            raise HTTPException(404, "Цель не найдена")
        photo = photo_base64(settings, target)
        if photo is None:
            raise HTTPException(404, "У цели нет сохранённого фото — обнови профиль")
        return photo

    async def intel(request: Request) -> dict[str, Any]:
        guard(request)
        query = (request.query_params.get("name") or request.query_params.get("q") or "").strip()
        names = request.query_params.get("names") or ""
        if not query and not names:
            raise HTTPException(400, "name: пустой запрос")
        values = [n.strip() for n in names.split(",") if n.strip()] if names else [query]
        return {"rows": [guess(value).as_dict() for value in values]}

    async def gift_art(request: Request) -> Response:
        guard(request)
        gift_id = int(request.path_params["gift_id"])
        art = await state.gift_art.get(gift_id)
        if not art:
            raise HTTPException(404, "Иллюстрация подарка недоступна")
        data, ext = art
        content_types = {"tgs": "application/x-tgsticker", "webp": "image/webp",
                         "png": "image/png", "jpg": "image/jpeg"}
        return Response(data, media_type=content_types[ext], headers={"Cache-Control": "private, max-age=86400"})

    async def gifts_list(request: Request) -> dict[str, Any]:
        guard(request)
        owner_raw = (request.query_params.get("owner") or "").strip()
        owner = int(owner_raw) if owner_raw.lstrip("-").isdigit() else 0
        nft_only = _q_bool(request, "nft")
        chain = _q_chain(request)
        query = (request.query_params.get("q") or "").strip() or None
        limit = _q_int(request, "limit", 100, 1, 1000)
        offset = _q_int(request, "offset", 0, 0, 1_000_000)
        rows = state.store.list_gifts(
            owner or None,
            nft_only=nft_only,
            chain=chain,
            q=query,
            limit=limit,
            offset=offset,
        )
        summary = state.store.gift_summary()
        return {
            "rows": rows,
            "summary": summary,
            "owners": state.store.gift_owners(20),
            "total": state.store.count_gifts(owner or None, nft_only=nft_only, chain=chain, q=query),
        }

    async def gifts_all_owners(request: Request) -> dict[str, Any]:
        guard(request)
        return {"rows": state.store.all_gift_owners(
            q=(request.query_params.get("q") or "").strip() or None,
            limit=_q_int(request, "limit", 100, 1, 1000),
            offset=_q_int(request, "offset", 0, 0, 1_000_000),
        )}

    async def gift_owner_nft(request: Request) -> dict[str, Any]:
        guard(request)
        owner_id = int(request.path_params["owner_id"])
        rows = state.store.query(
            "SELECT nft_items.*, (SELECT presence FROM gifts WHERE gifts.slug = nft_items.slug "
            "AND gifts.owner_tg_id = nft_items.owner_tg_id ORDER BY id LIMIT 1) AS owner_presence "
            "FROM nft_items WHERE owner_tg_id = ? ORDER BY seen_at DESC LIMIT 1000", (owner_id,)
        )
        return {"rows": rows}

    async def gift_owner_profile(request: Request) -> dict[str, Any]:
        """Профиль для просмотра без добавления человека в список слежки."""
        import base64

        from telethon import functions

        guard(request)
        owner_id = int(request.path_params["owner_id"])
        username = (request.query_params.get("username") or "").strip().lstrip("@")
        try:
            account = await state.pool.first()
            entity = await account.client.get_entity(username or owner_id)
            if int(getattr(entity, "id", 0)) != owner_id:
                raise ValueError("Имя пользователя теперь принадлежит другому аккаунту")
            result: dict[str, Any] = {
                "tg_id": owner_id, "username": getattr(entity, "username", None),
                "first_name": getattr(entity, "first_name", None),
                "last_name": getattr(entity, "last_name", None),
                "is_bot": bool(getattr(entity, "bot", False)),
                "is_deleted": bool(getattr(entity, "deleted", False)),
            }
            try:
                full = await account.client(functions.users.GetFullUserRequest(entity))
                result["bio"] = getattr(getattr(full, "full_user", None), "about", None)
            except Exception as exc:  # noqa: BLE001
                log.debug("профиль владельца %s: %s", owner_id, exc)
            try:
                raw = await account.client.download_profile_photo(entity, file=bytes)
                if raw and len(raw) <= 2_500_000:
                    result["photo"] = base64.b64encode(raw).decode("ascii")
            except Exception as exc:  # noqa: BLE001
                log.debug("фото владельца %s: %s", owner_id, exc)
            return result
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Не удалось получить профиль из Telegram: {exc}") from exc

    async def gifts_heavy(request: Request) -> dict[str, Any]:
        guard(request)
        return {"rows": state.store.heavy_gift_profiles(_q_int(request, "limit", 100, 1, 1000))}

    async def nft_history(request: Request) -> dict[str, Any]:
        guard(request)
        slug = request.path_params["slug"].strip()
        if not slug or len(slug) > 200:
            raise HTTPException(400, "Укажите название NFT")
        return {"rows": state.store.nft_history(slug)}

    async def gifts_nft(request: Request) -> dict[str, Any]:
        """NFT-инвентарь: все уникальные вещи с фильтром «кошелёк / Telegram»."""
        guard(request)
        chain = _q_chain(request)
        query = (request.query_params.get("q") or "").strip() or None
        sort = (request.query_params.get("sort") or "value").strip()
        limit = _q_int(request, "limit", 100, 1, 1000)
        offset = _q_int(request, "offset", 0, 0, 1_000_000)
        resale_only = _q_flag(request, "resell")
        collection = _q_collection(request)
        return {
            "rows": state.store.nft_items(
                chain=chain,
                q=query,
                sort=sort,
                limit=limit,
                offset=offset,
                resale_only=resale_only,
                collection=collection,
            ),
            "total": state.store.nft_count(
                chain=chain, q=query, resale_only=resale_only, collection=collection
            ),
            "summary": state.store.nft_summary(),
            "owners": state.store.nft_owners(chain=chain, limit=24),
            "collections": state.store.nft_collections(24),
            "chain": chain or "all",
        }

    async def gifts_nft_owners(request: Request) -> dict[str, Any]:
        """Владельцы NFT — почти все пользователи, которых собрали по сети."""
        guard(request)
        chain = _q_chain(request)
        query = (request.query_params.get("q") or "").strip() or None
        limit = _q_int(request, "limit", 100, 1, 1000)
        offset = _q_int(request, "offset", 0, 0, 1_000_000)
        return {
            "rows": state.store.nft_owners(chain=chain, q=query, limit=limit, offset=offset),
            "summary": state.store.nft_summary(),
        }

    async def gifts_nft_csv(request: Request) -> Response:
        """Выгрузка найденных NFT в CSV — открывается в Excel/Sheets."""
        guard(request)
        chain = _q_chain(request)
        query = (request.query_params.get("q") or "").strip() or None
        sort = (request.query_params.get("sort") or "value").strip()
        limit = _q_int(request, "limit", 5000, 1, 100_000)
        rows = state.store.nft_items(
            chain=chain,
            q=query,
            sort=sort,
            limit=limit,
            offset=0,
            resale_only=_q_flag(request, "resell"),
            collection=_q_collection(request),
        )
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow([
            "slug", "title", "num", "где_лежит", "owner_tg_id", "owner_username",
            "owner_name", "owner_address", "gift_address", "value_amount",
            "value_currency", "value_usd", "resell_amount", "issued", "total", "seen_at",
        ])
        for row in rows:
            writer.writerow([
                row.get("slug"),
                row.get("title"),
                row.get("num"),
                "кошелёк" if row.get("on_chain") else "telegram",
                row.get("owner_tg_id"),
                row.get("owner_username"),
                row.get("owner_name"),
                row.get("owner_address"),
                row.get("gift_address"),
                row.get("value_amount"),
                row.get("value_currency"),
                row.get("value_usd"),
                row.get("resell_amount"),
                row.get("availability_issued"),
                row.get("availability_total"),
                row.get("seen_at"),
            ])
        body = buffer.getvalue().encode("utf-8-sig")
        return Response(
            body,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="healdragg-nft.csv"'},
        )

    def launch_nft_chain_run(run_id: int) -> Job:
        run = state.store.nft_chain_run(run_id)
        if not run:
            raise ValueError("Задача поиска NFT не найдена")
        budget = int(run["budget"])
        job = Job(key="gifts:chain", kind="giftchain")
        job.state = {"phase": "восстанавливаю поиск", "done": int(run["used"]),
                     "total": budget, "found": int(run["new_items"]), "run_id": run_id}
        state.jobs[job.key] = job

        async def on_progress(progress: dict[str, Any]) -> None:
            current = state.store.nft_chain_run(run_id)
            if current:
                job.state = {**progress, "done": int(current["used"]), "total": budget,
                             "found": int(current["new_items"]), "run_id": run_id}

        async def runner() -> None:
            try:
                current = state.store.nft_chain_run(run_id)
                if not current:
                    raise ValueError("Задача поиска NFT не найдена")
                if int(current["used"]) >= budget and current.get("inflight"):
                    message = "Лимит исчерпан во время прерванного запроса. Для повтора начните новый поиск."
                    state.store.set_nft_chain_status(run_id, "paused", message)
                    job.error = message
                    return
                job.state = {**job.state, "phase": "ожидаю завершения другого обхода подарков"}
                async with state.gift_lock:
                    await crawl_nft_chain(
                        state.pool, state.store, settings, requests=budget, reporter=on_progress,
                        discover=bool(run["discover"]), slug=run["slug"], total=run["total"], run_id=run_id,
                    )
                current = state.store.nft_chain_run(run_id) or run
                state.store.set_nft_chain_status(run_id, "done")
                job.state = {"phase": "готово", "done": int(current["used"]), "total": budget,
                             "found": int(current["new_items"]), "errors": int(current["errors"]),
                             "run_id": run_id}
            except asyncio.CancelledError:
                # Стоп записывается обработчиком; закрытие процесса оставляет задачу активной.
                job.state = {**job.state, "phase": "остановлено"}
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("поиск NFT-коллекций прервался")
                job.error = str(exc)[:300] or "Не удалось продолжить поиск NFT"
                state.store.set_nft_chain_status(run_id, "error", job.error)
            finally:
                job.finished = True

        job.task = asyncio.create_task(runner(), name=job.key)
        return job

    async def nft_chain_status(request: Request) -> dict[str, Any]:
        guard(request)
        run = state.store.nft_chain_run()
        return {"task": {key: value for key, value in run.items() if key != "discovery_ids"} if run else None}

    async def resume_nft_chain(request: Request) -> dict[str, Any]:
        guard(request)
        run = state.store.nft_chain_run()
        if not run or run["status"] not in ("paused", "error"):
            raise HTTPException(409, "Нет приостановленного поиска NFT")
        if state.running("gifts:chain"):
            raise HTTPException(409, "Поиск NFT уже идёт")
        if not state.store.session_names():
            raise HTTPException(409, "Нет активного аккаунта Telegram")
        if int(run["used"]) >= int(run["budget"]):
            raise HTTPException(409, "Лимит исчерпан — начните новый поиск")
        state.store.set_nft_chain_status(int(run["id"]), "running")
        return launch_nft_chain_run(int(run["id"])).as_dict()

    async def start_nft_chain(request: Request) -> dict[str, Any]:
        """Массовый обход коллекций уникальных подарков: добираем владельцев."""
        guard(request)
        if not state.store.session_names():
            raise HTTPException(409, "Нет аккаунта-сканера — войдите в Telegram в приложении")
        if state.running("gifts:chain"):
            raise HTTPException(409, "Поиск NFT-коллекций уже идёт")
        body = await _body(request)
        requests_total = body.get("requests")
        requests_total = int(requests_total) if isinstance(requests_total, int) else 500
        requests_total = max(1, min(200_000, requests_total))
        discover = bool(body.get("discover", True))
        slug = str(body.get("slug") or "").strip() or None
        total = body.get("total")
        total = int(total) if isinstance(total, int) else None
        session = state.store.session_names()[0]
        session_id = state.store.scalar("SELECT tg_user_id FROM sessions WHERE name = ?", (session,))
        run_id = state.store.create_nft_chain_run(requests_total, discover, slug, total, session, session_id)
        return launch_nft_chain_run(run_id).as_dict()

    async def start_gift_refresh(request: Request) -> dict[str, Any]:
        """Обновляет уже собранные профили: новые подарки и цены NFT."""
        guard(request)
        if not state.store.session_names():
            raise HTTPException(409, "Нет аккаунта-сканера — войди в Telegram в приложении")
        if state.running("gifts:refresh"):
            raise HTTPException(409, "Обновление подарков уже идёт")
        body = await _body(request)
        limit = body.get("limit")
        limit = int(limit) if isinstance(limit, int) else 60
        limit = max(1, min(5000, limit))
        per_profile = body.get("per_profile")
        per_profile = int(per_profile) if isinstance(per_profile, int) else 100
        per_profile = max(10, min(5000, per_profile))
        if not state.store.gift_owners(1):
            raise HTTPException(400, "Подарков в базе нет — сначала соберите хотя бы один профиль")

        job = Job(key="gifts:refresh", kind="giftrefresh")
        job.state = {"phase": "старт", "done": 0, "total": limit, "found": 0}
        state.jobs["gifts:refresh"] = job

        async def on_progress(progress: dict[str, Any]) -> None:
            job.state = progress

        async def runner() -> None:
            try:
                job.state = {**job.state, "phase": "ожидаю завершения другого обхода подарков"}
                async with state.gift_lock:
                    result = await refresh_gifts(
                        state.pool,
                        state.store,
                        settings,
                        limit=limit,
                        per_profile=per_profile,
                        reporter=on_progress,
                    )
                job.state = {
                    **job.state,
                    "phase": "готово",
                    "done": int(result.get("owners", 0)),
                    "total": int(result.get("owners", 0)),
                    "found": int(result.get("new", 0)),
                    "summary": result,
                }
            except asyncio.CancelledError:
                job.state = {**job.state, "phase": "отменено"}
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("api gift refresh failed")
                job.error = str(exc)
            finally:
                job.finished = True

        job.task = asyncio.create_task(runner(), name="gifts:refresh")
        return job.as_dict()

    async def start_nft_profiles(request: Request) -> dict[str, Any]:
        """Читает подарки владельцев NFT — база растёт по живой сети дарителей."""
        guard(request)
        if not state.store.session_names():
            raise HTTPException(409, "Нет аккаунта-сканера — войди в Telegram в приложении")
        if state.running("gifts:nftprofiles"):
            raise HTTPException(409, "Обход профилей владельцев уже идёт")
        body = await _body(request)
        limit = body.get("limit")
        limit = int(limit) if isinstance(limit, int) else 200
        limit = max(1, min(20000, limit))
        depth = body.get("depth")
        depth = int(depth) if isinstance(depth, int) else 2
        depth = max(1, min(4, depth))
        per_profile = body.get("per_profile")
        per_profile = int(per_profile) if isinstance(per_profile, int) else 100
        per_profile = max(10, min(1000, per_profile))
        if not state.store.nft_summary()["owners"]:
            raise HTTPException(400, "Владельцев NFT ещё нет — сначала найдите NFT в коллекциях или профилях")

        job = Job(key="gifts:nftprofiles", kind="giftprofiles")
        job.state = {"phase": "старт", "done": 0, "total": limit, "found": 0}
        state.jobs["gifts:nftprofiles"] = job

        async def on_progress(progress: dict[str, Any]) -> None:
            job.state = progress

        async def runner() -> None:
            try:
                job.state = {**job.state, "phase": "ожидаю завершения другого обхода подарков"}
                async with state.gift_lock:
                    result = await crawl_nft_profiles(
                        state.pool,
                        state.store,
                        settings,
                        limit=limit,
                        depth=depth,
                        per_profile=per_profile,
                        reporter=on_progress,
                    )
                job.state = {
                    "phase": "готово",
                    "done": int(result.get("owners", 0)),
                    "total": int(result.get("owners", 0)),
                    "found": int(result.get("new", 0)),
                    "result": result,
                }
            except asyncio.CancelledError:
                job.state = {**job.state, "phase": "отменено"}
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("обход профилей владельцев NFT не удался")
                job.error = str(exc)
            finally:
                job.finished = True

        job.task = asyncio.create_task(runner(), name="gifts:nftprofiles")
        return job.as_dict()

    def launch_heavy_task(config: dict[str, Any]) -> Job:
        owner = int(config["owner_tg_id"])
        query = str(config["query"])
        session = str(config["session"])
        watch = bool(config["watch"])
        session_tg_id = config.get("session_tg_id")
        interval = int(config["interval_seconds"])
        key = f"gifts:heavy:{owner}"
        job = Job(key=key, kind="gift_heavy_watch" if watch else "gift_heavy", target_id=owner)
        job.state = {"phase": "восстанавливаю задачу", "done": 0, "total": 0, "found": 0,
                     "session": session, "watch": watch, "last_error": config.get("last_error")}
        state.jobs[key] = job

        async def on_progress(progress: dict[str, Any]) -> None:
            job.state = {**progress, "session": session, "watch": watch}

        async def runner() -> None:
            next_at = int(config.get("next_at") or 0)
            try:
                while True:
                    if session not in state.store.session_names():
                        job.error = "Выбранный аккаунт недоступен. Выберите активный аккаунт и запустите заново."
                        state.store.set_heavy_task_status(owner, "error", error=job.error)
                        break
                    current_account = state.store.scalar("SELECT tg_user_id FROM sessions WHERE name = ?", (session,))
                    if session_tg_id and (not current_account or int(current_account) != int(session_tg_id)):
                        job.error = "Аккаунт для задачи изменился. Выберите аккаунт и запустите заново."
                        state.store.set_heavy_task_status(owner, "error", error=job.error)
                        break
                    if next_at > now_ts():
                        job.state = {**job.state, "phase": "наблюдение · ожидание следующего прохода",
                                     "next_at": next_at}
                        await asyncio.sleep(max(0, next_at - now_ts()))
                        continue  # повторно проверяем доступность выбранного аккаунта
                    state.store.set_heavy_task_status(owner, "running")
                    try:
                        job.state = {**job.state, "phase": "ожидаю завершения другого обхода подарков"}
                        async with state.gift_lock:
                            result = await crawl_gifts(
                                state.pool, state.store, settings, query,
                                sessions=[session], depth=1, max_profiles=1,
                                per_profile=100_000, allow_heavy=True,
                                expected_owner=owner,
                                expected_session_user=int(session_tg_id) if session_tg_id else None,
                                reporter=on_progress,
                            )
                        info_result = result["results"][0]
                        if info_result.get("error"):
                            raise RuntimeError(info_result["error"])
                        complete = bool(info_result.get("complete"))
                        if not complete:
                            raise RuntimeError("Обход неполный — статусы подарков не менялись")
                        job.state = {"phase": "готово", "done": result["gifts"], "total": result["gifts"],
                                     "found": result["gifts"], "new": result["new"], "complete": True,
                                     "session": session, "watch": watch}
                    except ValueError as exc:
                        # Изменение владельца username или аккаунта нельзя исправлять повтором вслепую.
                        job.error = str(exc)[:300] or "Не удалось проверить владельца профиля"
                        state.store.set_heavy_task_status(owner, "error", error=job.error)
                        break
                    except Exception as exc:  # noqa: BLE001 — запоминаем ошибку и не теряем задачу
                        log.warning("обход большого профиля %s не удался: %s", owner, exc)
                        error = str(exc)[:300] or "Не удалось прочитать профиль"
                        if not watch:
                            job.error = error
                            state.store.set_heavy_task_status(owner, "error", error=error)
                            break
                        next_at = now_ts() + interval
                        state.store.set_heavy_task_status(owner, "running", next_at=next_at, error=error)
                        job.state = {**job.state, "phase": "ошибка · повтор позже", "last_error": error,
                                     "next_at": next_at, "session": session, "watch": watch}
                        continue
                    if not watch:
                        state.store.set_heavy_task_status(owner, "done")
                        break
                    next_at = now_ts() + interval
                    state.store.set_heavy_task_status(owner, "running", next_at=next_at)
                    job.state = {**job.state, "phase": "наблюдение · ожидание следующего прохода",
                                 "next_at": next_at}
            except asyncio.CancelledError:
                # Пользовательский стоп сохраняется заранее; завершение процесса
                # оставляет running для восстановления при следующем запуске.
                job.state = {**job.state, "phase": "остановлено"}
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("задача большого профиля прервана")
                job.error = str(exc)[:300] or "Не удалось продолжить задачу"
                state.store.set_heavy_task_status(owner, "error", error=job.error)
            finally:
                job.finished = True

        job.task = asyncio.create_task(runner(), name=key)
        return job

    async def failed_heavy_gifts(request: Request) -> dict[str, Any]:
        guard(request)
        return {"rows": state.store.failed_heavy_tasks(_q_int(request, "limit", 100, 1, 500))}

    async def dismiss_heavy_gift_error(request: Request) -> dict[str, Any]:
        guard(request)
        owner = int(request.path_params["owner_id"])
        config = state.store.heavy_task(owner)
        if not config or config["status"] != "error" or state.running(f"gifts:heavy:{owner}"):
            raise HTTPException(409, "Нет остановленной задачи с ошибкой")
        state.store.set_heavy_task_status(owner, "stopped")
        return {"ok": True}

    async def heavy_gift_status(request: Request) -> dict[str, Any]:
        guard(request)
        owner = int(request.path_params["owner_id"])
        return {"task": state.store.heavy_task(owner)}

    async def start_heavy_gifts(request: Request) -> dict[str, Any]:
        """Выбранный аккаунт: один полный проход или периодическое наблюдение."""
        guard(request)
        owner = int(request.path_params["owner_id"])
        body = await _body(request)
        session = str(body.get("session") or "").strip()
        if not session or session not in state.store.session_names():
            raise HTTPException(400, "Выберите активный аккаунт для этого профиля")
        info = state.store.query_one(
            "SELECT owner_username AS username FROM gifts WHERE owner_tg_id = ? "
            "AND owner_username IS NOT NULL ORDER BY seen_at DESC LIMIT 1", (owner,),
        ) or state.store.query_one(
            "SELECT owner_username AS username FROM nft_items WHERE owner_tg_id = ? "
            "AND owner_username IS NOT NULL ORDER BY seen_at DESC LIMIT 1", (owner,),
        ) or state.store.query_one(
            "SELECT username FROM gift_heavy_profiles WHERE owner_tg_id = ?", (owner,),
        )
        if info is None and not state.store.scalar(
            "SELECT EXISTS(SELECT 1 FROM gift_heavy_profiles WHERE owner_tg_id = ?) "
            "OR EXISTS(SELECT 1 FROM gifts WHERE owner_tg_id = ?) "
            "OR EXISTS(SELECT 1 FROM nft_items WHERE owner_tg_id = ?)", (owner, owner, owner), 0,
        ):
            raise HTTPException(404, "Профиль не найден в базе подарков")
        query = (info or {}).get("username") or str(owner)
        watch = body.get("watch") is True
        interval = body.get("interval_seconds")
        interval = max(60, min(86400, interval if isinstance(interval, int) else 900))
        key = f"gifts:heavy:{owner}"
        if state.running(key):
            raise HTTPException(409, "Этот профиль уже обрабатывается")
        session_tg_id = state.store.scalar("SELECT tg_user_id FROM sessions WHERE name = ?", (session,))
        state.store.save_heavy_task(owner, query, session, watch, interval, session_tg_id)
        config = state.store.heavy_task(owner)
        assert config is not None
        return launch_heavy_task(config).as_dict()

    async def gifts_graph(request: Request) -> dict[str, Any]:
        guard(request)
        seed_raw = (request.query_params.get("seed") or "").strip()
        seed = int(seed_raw) if seed_raw.lstrip("-").isdigit() else None
        return state.store.gift_graph(seed=seed)

    def launch_gift_crawl(run_id: int) -> Job:
        run = state.store.gift_crawl(run_id)
        if not run:
            raise ValueError("Обход не найден")
        key = f"gifts:run:{run_id}"
        job = Job(key=key, kind="gifts", target_id=run.get("expected_owner"))
        job.state = {"phase": "ожидаю очередь", "done": 0, "total": 0, "found": 0, "run_id": run_id}
        state.jobs[key] = job

        async def on_progress(progress: dict[str, Any]) -> None:
            counts = state.store.gift_crawl_counts(run_id)
            job.state = {**progress, "run_id": run_id, "queue": counts,
                         "done": counts["done"] + counts["unavailable"] + counts["deferred"],
                         "total": counts["total"]}

        async def runner() -> None:
            try:
                job.state = {**job.state, "phase": "ожидаю завершения другого обхода подарков"}
                async with state.gift_lock:
                    result = await crawl_gifts(
                        state.pool, state.store, settings, run["query"],
                        depth=int(run["depth"]), max_profiles=int(run["max_profiles"]),
                        per_profile=int(run["per_profile"]), reporter=on_progress,
                        skip_known=not bool(run.get("refresh_known")), run_id=run_id,
                        expected_owner=run.get("expected_owner"),
                    )
                counts = state.store.gift_crawl_counts(run_id)
                status = "paused" if counts["pending"] else "done"
                state.store.gift_crawl_status(run_id, status)
                job.state = {"phase": "остановлено по лимиту" if status == "paused" else "готово",
                             "done": counts["done"] + counts["unavailable"] + counts["deferred"],
                             "total": counts["total"],
                             "found": result["gifts"], "new": result["new"],
                             "queue": counts, "run_id": run_id}
            except asyncio.CancelledError:
                # Пользовательская остановка уже записана в БД обработчиком cancel;
                # при завершении процесса состояние остаётся running для восстановления.
                job.state = {**job.state, "phase": "остановлено"}
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("сбор подарков не удался")
                job.error = str(exc)
                state.store.gift_crawl_status(run_id, "error", str(exc)[:300])
            finally:
                job.finished = True

        job.task = asyncio.create_task(runner(), name=key)
        return job

    async def gift_crawl_runs(request: Request) -> dict[str, Any]:
        guard(request)
        limit = _q_int(request, "limit", 100, 1, 500)
        offset = _q_int(request, "offset", 0, 0, 1_000_000)
        runs = (state.store.gift_crawls_needing_attention(limit=limit, offset=offset)
                if _q_bool(request, "attention") else
                state.store.gift_crawls(("running", "paused", "error", "done"), limit=limit, offset=offset))
        return {"rows": [{**run, "queue": state.store.gift_crawl_counts(int(run["id"]))} for run in runs],
                "offset": offset, "has_more": len(runs) == limit}

    async def gift_crawl_issues(request: Request) -> dict[str, Any]:
        guard(request)
        run_id = int(request.path_params["run_id"])
        if not state.store.gift_crawl(run_id):
            raise HTTPException(404, "Обход не найден")
        limit = _q_int(request, "limit", 50, 1, 100)
        offset = _q_int(request, "offset", 0, 0, 1_000_000)
        counts = state.store.gift_crawl_counts(run_id)
        return {"rows": state.store.gift_crawl_issues(run_id, limit=limit, offset=offset),
                "total": counts["unavailable"] + counts["deferred"], "offset": offset}

    async def resume_gift_crawl(request: Request) -> dict[str, Any]:
        guard(request)
        run_id = int(request.path_params["run_id"])
        run = state.store.gift_crawl(run_id)
        if not run:
            raise HTTPException(404, "Обход не найден")
        if not state.store.session_names():
            raise HTTPException(409, "Нет активного аккаунта Telegram")
        if state.running(f"gifts:run:{run_id}"):
            raise HTTPException(409, "Обход уже идёт")
        # Сначала проходим ещё не обработанную очередь, иначе повторные ошибки
        # могут бесконечно вытеснять новые профили при небольшом лимите.
        if not state.store.gift_crawl_counts(run_id)["pending"]:
            with state.store.tx() as conn:
                conn.execute("UPDATE gift_crawl_queue SET state = 'pending', error = NULL "
                             "WHERE run_id = ? AND state = 'unavailable'", (run_id,))
        state.store.gift_crawl_status(run_id, "running")
        return launch_gift_crawl(run_id).as_dict()

    async def collect_profile_gifts(request: Request) -> dict[str, Any]:
        guard(request)
        tg_id = request.path_params["tg_id"]
        target = state.store.get_target(tg_id)
        if not target:
            raise HTTPException(404, "Профиль не найден")
        if not state.store.session_names():
            raise HTTPException(409, "Нет активного аккаунта Telegram")
        if any(job.kind == "gifts" and job.target_id == tg_id and not job.finished for job in state.jobs.values()):
            raise HTTPException(409, "Сбор этого профиля уже выполняется")
        query = target.get("username") or str(tg_id)
        run_id = state.store.create_gift_crawl(query, 1, 1, 1000, refresh_known=True, expected_owner=tg_id)
        return launch_gift_crawl(run_id).as_dict()

    async def start_gifts(request: Request) -> dict[str, Any]:
        guard(request)
        body = await _body(request)
        query = str(body.get("query") or "").strip()
        if not query and body.get("existing") is True:
            rows = state.store.gift_owners(1)
            if rows:
                query = rows[0].get("owner_username") or str(rows[0]["owner_tg_id"])
        if not query:
            raise HTTPException(400, "Укажите профиль или продолжите сеть из существующей базы")
        if not state.store.session_names():
            raise HTTPException(409, "Нет аккаунта-сканера — войди в Telegram в приложении")
        depth = body.get("depth")
        depth = max(1, min(8, int(depth) if isinstance(depth, int) else 1))
        max_profiles = body.get("max_profiles")
        max_profiles = max(1, min(50_000, int(max_profiles) if isinstance(max_profiles, int) else 5))
        per_profile = body.get("per_profile")
        per_profile = max(10, min(5000, int(per_profile) if isinstance(per_profile, int) else 100))
        refresh_known = body.get("refresh_known") is True
        run_id = state.store.create_gift_crawl(query, depth, max_profiles, per_profile, refresh_known=refresh_known)
        return launch_gift_crawl(run_id).as_dict()

    async def start_scan(request: Request) -> dict[str, Any]:
        guard(request)
        tg_id = request.path_params["tg_id"]
        target = state.store.get_target(tg_id)
        if not target:
            raise HTTPException(404, "Цель не найдена")
        if not state.store.session_names():
            raise HTTPException(409, "Нет аккаунта-сканера — войди в Telegram в приложении")
        key = f"scan:{tg_id}"
        if state.running(key):
            raise HTTPException(409, "Скан уже идёт")
        body = await _body(request)
        deep = bool(body.get("deep", False))
        max_groups = body.get("max_groups")
        if max_groups is not None and not isinstance(max_groups, int):
            max_groups = None
        sessions_in = body.get("sessions")
        if not (isinstance(sessions_in, list) and all(isinstance(s, str) for s in sessions_in)):
            sessions_in = None

        job = Job(key=key, kind="scan", target_id=tg_id)
        job.state = {"phase": "старт", "done": 0, "total": 0, "found": 0}
        state.jobs[key] = job

        async def on_progress(progress: dict[str, Any]) -> None:
            job.state = progress

        async def runner() -> None:
            try:
                await scan_target(
                    state.pool,
                    state.store,
                    settings,
                    target,
                    sessions=sessions_in,
                    max_groups=max_groups,
                    deep=deep,
                    reporter=on_progress,
                )
                job.state = {**job.state, "phase": "готово"}
            except asyncio.CancelledError:
                job.state = {**job.state, "phase": "отменено"}
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("api scan failed")
                job.error = str(exc)
            finally:
                job.finished = True

        job.task = asyncio.create_task(runner(), name=key)
        return job.as_dict()

    async def catalog(request: Request) -> dict[str, Any]:
        guard(request)
        q = request.query_params.get("q") or None
        total = state.store.count_groups(q)
        pg = Page.build(total, _q_int(request, "page", 1, 1, 10_000), _q_int(request, "size", 20, 1, 100))
        rows = state.store.list_groups(page=pg.page, size=pg.size, query=q)
        return {
            "total": total,
            "page": pg.page,
            "pages": pg.pages,
            "rows": [{**r, "is_public": bool(r.get("is_public"))} for r in rows],
            "text": render_catalog(rows, page=pg),
        }

    async def catalog_parse(request: Request) -> dict[str, Any]:
        """Локальный разбор вставленного списка: без Telegram и без записи в базу."""
        guard(request)
        body = await _body(request)
        text = str(body.get("text") or "")
        if not text.strip():
            raise HTTPException(400, "text: вставь ссылки или @username")
        if len(text) > 200_000:
            raise HTTPException(400, "Слишком большой список: максимум 200 000 символов")
        payload = parse_batch(text)
        payload["sessions"] = state.store.session_names()
        return payload

    async def catalog_import(request: Request) -> dict[str, Any]:
        """Проверяет список через Telegram и добавляет чаты в базу (job)."""
        guard(request)
        body = await _body(request)
        text = str(body.get("text") or "")
        if not text.strip():
            raise HTTPException(400, "text: вставь ссылки или @username")
        preview = parse_batch(text)
        if not preview["items"]:
            raise HTTPException(400, "В списке нет валидных чатов — проверь ссылки")
        if not state.store.session_names():
            raise HTTPException(409, "Нет аккаунта-сканера — войди в Telegram в приложении")
        if state.running("import"):
            raise HTTPException(409, "Импорт уже идёт — дождись окончания")
        join = bool(body.get("join", False))
        archive = bool(body.get("archive", True))
        session = body.get("session") if isinstance(body.get("session"), str) else None

        job = Job(key="import", kind="import")
        job.state = {
            "phase": "старт",
            "done": 0,
            "total": len(preview["items"]),
            "found": 0,
            "preview": preview["stats"],
        }
        state.jobs["import"] = job

        async def on_progress(progress: dict[str, Any]) -> None:
            job.state = {**job.state, **progress}

        async def runner() -> None:
            try:
                result = await import_groups(
                    state.pool,
                    state.store,
                    text,
                    session=session,
                    join=join,
                    archive=archive,
                    reporter=on_progress,
                )
                job.state = {
                    **job.state,
                    "phase": "готово",
                    "done": len(preview["items"]),
                    "found": result["stats"]["added"],
                    "summary": result["stats"],
                    "rows": result["rows"],
                }
            except asyncio.CancelledError:
                job.state = {**job.state, "phase": "отменено"}
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("api manual import failed")
                job.error = str(exc)
            finally:
                job.finished = True

        job.task = asyncio.create_task(runner(), name="import")
        return job.as_dict()

    async def catalog_update(request: Request) -> dict[str, Any]:
        guard(request)
        tg_id = int(request.path_params["tg_id"])
        row = state.store.get_group(tg_id)
        if not row:
            raise HTTPException(404, "Чат не найден в базе")
        body = await _body(request)
        title = body.get("title")
        username = body.get("username")
        link = body.get("link")
        members = body.get("members_count")
        if title is not None and not isinstance(title, str):
            raise HTTPException(400, "title: нужна строка")
        if username is not None and not isinstance(username, str):
            raise HTTPException(400, "username: нужна строка")
        if link is not None and not isinstance(link, str):
            raise HTTPException(400, "link: нужна строка")
        if members is not None and not isinstance(members, int):
            raise HTTPException(400, "members_count: нужно число")
        state.store.update_group(
            tg_id,
            title=title,
            username=username,
            link=link,
            members_count=members,
        )
        return {"ok": True, "group": state.store.get_group(tg_id)}

    async def catalog_delete(request: Request) -> dict[str, Any]:
        guard(request)
        tg_id = int(request.path_params["tg_id"])
        if not state.store.get_group(tg_id):
            raise HTTPException(404, "Чат не найден в базе")
        state.store.delete_group(tg_id)
        return {"ok": True, "deleted": tg_id}

    async def find(request: Request) -> dict[str, Any]:
        guard(request)
        q = (request.query_params.get("q") or "").strip()
        if not q:
            raise HTTPException(400, "q: пустой запрос")
        rows = state.store.search_groups(q, limit=25)
        return {"query": q, "rows": rows, "total": len(rows)}

    async def jobs(request: Request) -> list[dict[str, Any]]:
        guard(request)
        return [job.as_dict() for job in state.jobs.values()]

    async def cancel(request: Request) -> dict[str, Any]:
        guard(request)
        key = str(request.path_params["key"])
        if key == "gifts:chain" and state.running(key):
            run = state.store.nft_chain_run()
            if run and run["status"] == "running":
                state.store.set_nft_chain_status(int(run["id"]), "paused")
        if key.startswith("gifts:heavy:") and state.running(key):
            try:
                state.store.set_heavy_task_status(int(key.rsplit(":", 1)[-1]), "stopped")
            except ValueError:
                pass
        if key.startswith("gifts:run:") and state.running(key):
            try:
                state.store.gift_crawl_status(int(key.rsplit(":", 1)[-1]), "paused")
            except ValueError:
                pass
        return {"ok": await state.cancel(key)}

    async def start_bootstrap(request: Request) -> dict[str, Any]:
        guard(request)
        if not state.store.session_names():
            raise HTTPException(409, "Нет аккаунта-сканера — войди в Telegram в приложении")
        if state.running("boot"):
            raise HTTPException(409, "Сбор уже идёт")
        join = _q_bool(request, "join")
        job = Job(key="boot", kind="bootstrap")
        job.state = {"phase": "старт", "done": 0, "total": 0, "found": 0}
        state.jobs["boot"] = job

        async def on_progress(progress: dict[str, Any]) -> None:
            job.state = progress

        async def runner() -> None:
            try:
                await bootstrap(
                    state.pool,
                    state.store,
                    settings,
                    join=join or settings.bootstrap_join,
                    join_limit=settings.join_limit,
                    reporter=on_progress,
                )
                job.state = {**job.state, "phase": "готово"}
            except asyncio.CancelledError:
                job.state = {**job.state, "phase": "отменено"}
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("api bootstrap failed")
                job.error = str(exc)
            finally:
                job.finished = True

        job.task = asyncio.create_task(runner(), name="boot")
        return job.as_dict()

    async def export(request: Request) -> JSONResponse:
        guard(request)
        target = state.store.get_target(request.path_params["tg_id"])
        if not target:
            raise HTTPException(404, "Цель не найдена")
        return JSONResponse(state.store.export_target(int(target["id"])))

    # ── веб-панель ─────────────────────────────────────────────────────────
    async def index(_: Request) -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    async def manifest(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "name": "Healdragg",
                "short_name": "Healdragg",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#05070d",
                "theme_color": "#05070d",
                "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
            }
        )

    async def graph_js(_: Request) -> FileResponse:
        return FileResponse(WEB_DIR / "graph.js", media_type="application/javascript")

    async def icon(_: Request) -> Response:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect width="64" height="64" rx="16" fill="#12121a"/>'
            '<text x="32" y="44" font-size="36" text-anchor="middle">🩺</text></svg>'
        )
        return Response(svg, media_type="image/svg+xml")

    async def docs(_: Request) -> Response:
        rows = sorted(
            (
                (", ".join(sorted(route.methods or [])), route.path)
                for route in ROUTES
                if route.path.startswith("/api")
            ),
            key=lambda r: (r[1], r[0]),
        )
        body = "".join(f"<tr><td>{m}</td><td><code>{p}</code></td></tr>" for m, p in rows)
        html = (
            "<!doctype html><meta charset=utf-8><title>Healdragg API</title>"
            "<style>body{background:#05070d;color:#e8f0ff;font:14px ui-monospace,monospace;padding:24px}"
            "td{padding:4px 12px;border-bottom:1px solid #1d2b44}code{color:#22d3ee}</style>"
            "<h2>Healdragg API</h2><p>Токен: <code>X-Api-Token</code> или <code>?token=</code></p>"
            f"<table>{body}</table>"
        )
        return Response(html, media_type="text/html")

    ROUTES = [
        Route("/api/health", _route(health)),
        Route("/api/stats", _route(stats)),
        Route("/api/sessions", _route(sessions)),
        Route("/api/sessions/import", _route(session_import), methods=["POST"]),
        Route("/api/graph/global", _route(graph_global)),
        Route("/api/settings/display", _route(display_get)),
        Route("/api/settings/display", _route(display_post), methods=["POST"]),
        Route("/api/backup", _route(backup_list)),
        Route("/api/backup/create", _route(backup_create), methods=["POST"]),
        Route("/api/backup/download", _route(backup_download)),
        Route("/api/backup/restore", _route(backup_restore), methods=["POST"]),
        Route("/api/session/start", _route(session_start), methods=["POST"]),
        Route("/api/session/code", _route(session_code), methods=["POST"]),
        Route("/api/session/password", _route(session_password), methods=["POST"]),
        Route("/api/session/{flow_id}/cancel", _route(session_cancel), methods=["POST"]),
        Route("/api/sessions/{name}", _route(delete_session), methods=["DELETE"]),
        Route("/api/profiles/open", _route(open_profile), methods=["POST"]),
        Route("/api/targets", _route(targets), methods=["GET"]),
        Route("/api/targets", _route(add_target), methods=["POST"]),
        Route("/api/targets/page", _route(targets_page), methods=["GET"]),
        Route("/api/targets/lookup", _route(target_lookup), methods=["GET"]),
        Route("/api/targets/{tg_id:int}", _route(get_target), methods=["GET"]),
        Route("/api/targets/{tg_id:int}", _route(delete_target), methods=["DELETE"]),
        Route("/api/targets/{tg_id:int}/groups", _route(target_groups)),
        Route("/api/targets/{tg_id:int}/gifts", _route(profile_gifts)),
        Route("/api/targets/{tg_id:int}/gifts/collect", _route(collect_profile_gifts), methods=["POST"]),
        Route("/api/search/gifts", _route(search_gifts)),
        Route("/api/search/people", _route(search_people)),
        Route("/api/search/nft", _route(search_unique_gift), methods=["POST"]),
        Route("/api/targets/{tg_id:int}/links", _route(target_links)),
        Route("/api/targets/{tg_id:int}/graph", _route(target_graph)),
        Route("/api/targets/{tg_id:int}/activity", _route(target_activity)),
        Route("/api/targets/{tg_id:int}/activity/collect", _route(collect_target_activity), methods=["POST"]),
        Route("/api/targets/{tg_id:int}/activity/live", _route(live_activity), methods=["POST"]),
        Route("/api/targets/{tg_id:int}/scan", _route(start_scan), methods=["POST"]),
        Route("/api/targets/{tg_id:int}/profile/refresh", _route(refresh_target_profile), methods=["POST"]),
        Route("/api/targets/{tg_id:int}/photo", _route(target_photo)),
        Route("/api/gifts", _route(gifts_list)),
        Route("/api/gifts/art/{gift_id:int}", _route(gift_art)),
        Route("/api/gifts/graph", _route(gifts_graph)),
        Route("/api/gifts/heavy", _route(gifts_heavy)),
        Route("/api/gifts/heavy/tasks", _route(failed_heavy_gifts)),
        Route("/api/gifts/owners", _route(gifts_all_owners)),
        Route("/api/gifts/owners/{owner_id:int}/profile", _route(gift_owner_profile)),
        Route("/api/gifts/owners/{owner_id:int}/heavy", _route(start_heavy_gifts), methods=["POST"]),
        Route("/api/gifts/owners/{owner_id:int}/heavy", _route(heavy_gift_status), methods=["GET"]),
        Route("/api/gifts/owners/{owner_id:int}/heavy/dismiss", _route(dismiss_heavy_gift_error), methods=["POST"]),
        Route("/api/gifts/owners/{owner_id:int}/nft", _route(gift_owner_nft)),
        Route("/api/gifts/nft/{slug}/history", _route(nft_history)),
        Route("/api/gifts/nft", _route(gifts_nft)),
        Route("/api/gifts/nft/owners", _route(gifts_nft_owners)),
        Route("/api/gifts/nft/export.csv", _route(gifts_nft_csv)),
        Route("/api/gifts/chain", _route(start_nft_chain), methods=["POST"]),
        Route("/api/gifts/chain", _route(nft_chain_status), methods=["GET"]),
        Route("/api/gifts/chain/resume", _route(resume_nft_chain), methods=["POST"]),
        Route("/api/gifts/nft/profiles", _route(start_nft_profiles), methods=["POST"]),
        Route("/api/gifts/crawl", _route(start_gifts), methods=["POST"]),
        Route("/api/gifts/runs", _route(gift_crawl_runs)),
        Route("/api/gifts/runs/{run_id:int}/issues", _route(gift_crawl_issues)),
        Route("/api/gifts/runs/{run_id:int}/resume", _route(resume_gift_crawl), methods=["POST"]),
        Route("/api/gifts/refresh", _route(start_gift_refresh), methods=["POST"]),
        Route("/api/intel", _route(intel)),
        Route("/api/alerts", _route(alerts)),
        Route("/api/alerts/read", _route(alerts_read), methods=["POST"]),
        Route("/api/catalog", _route(catalog)),
        Route("/api/catalog/parse", _route(catalog_parse), methods=["POST"]),
        Route("/api/catalog/import", _route(catalog_import), methods=["POST"]),
        Route("/api/catalog/{tg_id:int}/edit", _route(catalog_update), methods=["POST"]),
        Route("/api/catalog/{tg_id:int}", _route(catalog_delete), methods=["DELETE"]),
        Route("/api/find", _route(find)),
        Route("/api/jobs", _route(jobs)),
        Route("/api/jobs/{key}/cancel", _route(cancel), methods=["POST"]),
        Route("/api/bootstrap", _route(start_bootstrap), methods=["POST"]),
        Route("/api/export/{tg_id:int}", _route(export)),
        Route("/api/docs", _route(docs)),
        Route("/", _route(index)),
        Route("/manifest.webmanifest", _route(manifest)),
        Route("/graph.js", _route(graph_js)),
        Route("/icon.svg", _route(icon)),
    ]

    app = Starlette(routes=ROUTES, lifespan=lifespan)
    app.state.hd = state
    app.add_middleware(RestoreGate, state=state)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


def main() -> None:
    import uvicorn

    from .config import load_settings

    settings = load_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


__all__ = ["create_app", "main"]
