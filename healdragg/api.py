"""REST API + веб-панель (её же открывает Android-приложение).

Токен берётся из ``API_TOKEN`` (генерируется при первом запуске).
Передавай его заголовком ``X-Api-Token`` или параметром ``?token=``.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
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
from .config import Settings
from .db import Store
from .graph import build_graph
from .login import LoginError, LoginManager
from .scan import bootstrap, resolve_and_store_target, scan_target
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
        self.store = Store(settings.db_path)
        self.pool = SessionPool(settings, self.store)
        self.login = LoginManager(settings, self.store)
        self.jobs: dict[str, Job] = {}

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
        except Exception:  # noqa: BLE001
            pass
        job.finished = True
        job.state = {**job.state, "phase": "отменено"}
        return True

    async def close(self) -> None:
        for job in list(self.jobs.values()):
            if job.task and not job.task.done():
                job.task.cancel()
        await self.login.close()
        await self.pool.close()
        self.store.close()


def _target_payload(store: Store, target: dict[str, Any]) -> dict[str, Any]:
    stats = store.target_stats(int(target["id"]))
    return {
        **{k: v for k, v in target.items() if k != "access_hash"},
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
    """JSON-ответ по умолчанию: dict/list → JSONResponse, Response — как есть."""

    @wraps(fn)
    async def wrapper(request: Request) -> Any:
        result = await fn(request)
        if isinstance(result, Response):
            return result
        return JSONResponse(result)

    return wrapper


def create_app(settings: Settings) -> Starlette:
    token = settings.ensure_api_token()
    state = AppState(settings)

    @asynccontextmanager
    async def lifespan(_: Starlette):
        autopilot_task: asyncio.Task | None = None
        if settings.autopilot_minutes > 0:
            from .autopilot import loop as autopilot_loop

            autopilot_task = asyncio.create_task(
                autopilot_loop(state.pool, state.store, settings), name="autopilot"
            )
            log.info("автопилот: каждые %s мин", settings.autopilot_minutes)
        try:
            yield
        finally:
            if autopilot_task:
                autopilot_task.cancel()
                try:
                    await autopilot_task
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

    async def session_start(request: Request) -> dict[str, Any]:
        guard(request)
        body = await _body(request)
        phone = str(body.get("phone") or "")
        name = str(body.get("name") or "main")
        try:
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
        state.store.remove_session(name)
        session_file = settings.session_file(name)
        try:
            session_file.unlink(missing_ok=True)
        except OSError:
            log.warning("не удалось удалить %s", session_file)
        state.store.log_event("info", f"сессия {name} удалена", "app")
        return {"ok": True, "name": name}

    # ── цели ────────────────────────────────────────────────────────────────
    async def targets(request: Request) -> list[dict[str, Any]]:
        guard(request)
        return [_target_payload(state.store, t) for t in state.store.list_targets(100)]

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
        rows = state.store.all_target_groups(int(target["id"]))
        offset = _q_int(request, "offset", 0, 0, 1_000_000)
        limit = _q_int(request, "limit", 100, 1, 500)
        chunk = rows[offset : offset + limit]
        return {
            "total": len(rows),
            "offset": offset,
            "has_more": offset + len(chunk) < len(rows),
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
        if state.running(key):
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
        return {"ok": await state.cancel(str(request.path_params["key"]))}

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
        Route("/api/session/start", _route(session_start), methods=["POST"]),
        Route("/api/session/code", _route(session_code), methods=["POST"]),
        Route("/api/session/password", _route(session_password), methods=["POST"]),
        Route("/api/session/{flow_id}/cancel", _route(session_cancel), methods=["POST"]),
        Route("/api/sessions/{name}", _route(delete_session), methods=["DELETE"]),
        Route("/api/targets", _route(targets), methods=["GET"]),
        Route("/api/targets", _route(add_target), methods=["POST"]),
        Route("/api/targets/{tg_id:int}", _route(get_target), methods=["GET"]),
        Route("/api/targets/{tg_id:int}", _route(delete_target), methods=["DELETE"]),
        Route("/api/targets/{tg_id:int}/groups", _route(target_groups)),
        Route("/api/targets/{tg_id:int}/links", _route(target_links)),
        Route("/api/targets/{tg_id:int}/graph", _route(target_graph)),
        Route("/api/targets/{tg_id:int}/activity", _route(target_activity)),
        Route("/api/targets/{tg_id:int}/activity/collect", _route(collect_target_activity), methods=["POST"]),
        Route("/api/targets/{tg_id:int}/scan", _route(start_scan), methods=["POST"]),
        Route("/api/alerts", _route(alerts)),
        Route("/api/alerts/read", _route(alerts_read), methods=["POST"]),
        Route("/api/catalog", _route(catalog)),
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
