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
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .activity import collect_activity
from .config import Settings
from .db import Store
from .graph import build_graph
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


# ── схемы ──────────────────────────────────────────────────────────────────
class TargetIn(BaseModel):
    query: str = Field(min_length=1, max_length=128)


class AlertsReadIn(BaseModel):
    ids: list[int] | None = None


class ScanIn(BaseModel):
    deep: bool = False
    max_groups: int | None = None
    sessions: list[str] | None = None


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


def create_app(settings: Settings) -> FastAPI:
    token = settings.ensure_api_token()
    state = AppState(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
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

    app = FastAPI(
        title="Healdragg",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.hd = state
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def guard(request: Request) -> None:
        supplied = (
            request.headers.get("x-api-token")
            or request.query_params.get("token")
            or request.cookies.get("hd_token")
            or ""
        )
        if not supplied or not hmac.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="Неверный токен")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "time": now_ts()}

    @app.get("/api/stats", dependencies=[Depends(guard)])
    async def stats() -> dict[str, Any]:
        data = state.store.stats()
        return {
            **data,
            "db_human": human_size(data.get("db_bytes") or 0),
            "text": render_stats(data),
            "counts": state.store.table_counts(),
        }

    @app.get("/api/sessions", dependencies=[Depends(guard)])
    async def sessions() -> list[dict[str, Any]]:
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

    @app.get("/api/targets", dependencies=[Depends(guard)])
    async def targets() -> list[dict[str, Any]]:
        return [_target_payload(state.store, t) for t in state.store.list_targets(100)]

    @app.post("/api/targets", dependencies=[Depends(guard)])
    async def add_target(payload: TargetIn) -> dict[str, Any]:
        if not state.store.session_names():
            raise HTTPException(409, "Нет сессий. Добавь: healdragg session add main")
        try:
            target = await resolve_and_store_target(state.pool, state.store, payload.query)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Не удалось найти аккаунт: {exc}") from exc
        return _target_payload(state.store, target)

    @app.get("/api/targets/{tg_id}", dependencies=[Depends(guard)])
    async def get_target(tg_id: int) -> dict[str, Any]:
        target = state.store.get_target(tg_id)
        if not target:
            raise HTTPException(404, "Цель не найдена")
        return _target_payload(state.store, target)

    @app.delete("/api/targets/{tg_id}", dependencies=[Depends(guard)])
    async def delete_target(tg_id: int) -> dict[str, Any]:
        state.store.delete_target(tg_id)
        return {"ok": True}

    @app.get("/api/targets/{tg_id}/groups", dependencies=[Depends(guard)])
    async def target_groups(
        tg_id: int,
        page: int = Query(1, ge=1),
        size: int = Query(15, ge=1, le=100),
        order: str = Query("last", pattern="^(last|members|title|found)$"),
    ) -> dict[str, Any]:
        target = state.store.get_target(tg_id)
        if not target:
            raise HTTPException(404, "Цель не найдена")
        return _groups_payload(state.store, target, page=page, size=size, order=order)

    @app.get("/api/alerts", dependencies=[Depends(guard)])
    async def alerts(limit: int = Query(50, ge=1, le=200), unread: bool = False) -> dict[str, Any]:
        return {
            "rows": state.store.list_alerts(limit=limit, unread_only=unread),
            "unread": state.store.count_alerts(),
        }

    @app.post("/api/alerts/read", dependencies=[Depends(guard)])
    async def alerts_read(payload: AlertsReadIn | None = None) -> dict[str, Any]:
        changed = state.store.mark_alerts_read((payload or AlertsReadIn()).ids)
        return {"ok": True, "changed": changed, "unread": state.store.count_alerts()}

    @app.get("/api/targets/{tg_id}/links", dependencies=[Depends(guard)])
    async def target_links(
        tg_id: int,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        target = state.store.get_target(tg_id)
        if not target:
            raise HTTPException(404, "Цель не найдена")
        rows = state.store.all_target_groups(int(target["id"]))
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

    @app.get("/api/targets/{tg_id}/graph", dependencies=[Depends(guard)])
    async def target_graph(tg_id: int, max_peers: int = Query(200, ge=10, le=500)) -> dict[str, Any]:
        target = state.store.get_target(tg_id)
        if not target:
            raise HTTPException(404, "Цель не найдена")
        return build_graph(state.store, int(target["id"]), max_peers=max_peers)

    @app.get("/api/targets/{tg_id}/activity", dependencies=[Depends(guard)])
    async def target_activity(tg_id: int, limit: int = Query(60, ge=1, le=300)) -> dict[str, Any]:
        target = state.store.get_target(tg_id)
        if not target:
            raise HTTPException(404, "Цель не найдена")
        target_pk = int(target["id"])
        return {
            "stats": state.store.activity_stats(target_pk),
            "rows": state.store.activity_feed(target_pk, limit=limit),
            "edges": state.store.target_edges(target_pk, limit=50),
        }

    @app.post("/api/targets/{tg_id}/activity/collect", dependencies=[Depends(guard)])
    async def collect_target_activity(
        tg_id: int,
        days: int = Query(30, ge=1, le=365),
        per_chat: int = Query(40, ge=1, le=200),
        max_chats: int = Query(80, ge=1, le=500),
    ) -> dict[str, Any]:
        target = state.store.get_target(tg_id)
        if not target:
            raise HTTPException(404, "Цель не найдена")
        if not state.store.session_names():
            raise HTTPException(409, "Нет сессий. Добавь: healdragg session add main")
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
                    days=days,
                    per_chat=per_chat,
                    max_chats=max_chats,
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

    @app.post("/api/targets/{tg_id}/scan", dependencies=[Depends(guard)])
    async def start_scan(tg_id: int, payload: ScanIn | None = None) -> dict[str, Any]:
        target = state.store.get_target(tg_id)
        if not target:
            raise HTTPException(404, "Цель не найдена")
        if not state.store.session_names():
            raise HTTPException(409, "Нет сессий. Добавь: healdragg session add main")
        key = f"scan:{tg_id}"
        if state.running(key):
            raise HTTPException(409, "Скан уже идёт")
        payload = payload or ScanIn()
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
                    sessions=payload.sessions,
                    max_groups=payload.max_groups,
                    deep=payload.deep,
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

    @app.get("/api/catalog", dependencies=[Depends(guard)])
    async def catalog(
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=100),
        q: str | None = None,
    ) -> dict[str, Any]:
        total = state.store.count_groups(q)
        pg = Page.build(total, page, size)
        rows = state.store.list_groups(page=pg.page, size=size, query=q)
        return {
            "total": total,
            "page": pg.page,
            "pages": pg.pages,
            "rows": [{**r, "is_public": bool(r.get("is_public"))} for r in rows],
            "text": render_catalog(rows, page=pg),
        }

    @app.get("/api/find", dependencies=[Depends(guard)])
    async def find(q: str = Query(min_length=1)) -> dict[str, Any]:
        rows = state.store.search_groups(q, limit=25)
        return {"query": q, "rows": rows, "total": len(rows)}

    @app.get("/api/jobs", dependencies=[Depends(guard)])
    async def jobs() -> list[dict[str, Any]]:
        return [job.as_dict() for job in state.jobs.values()]

    @app.post("/api/jobs/{key}/cancel", dependencies=[Depends(guard)])
    async def cancel(key: str) -> dict[str, Any]:
        return {"ok": await state.cancel(key)}

    @app.post("/api/bootstrap", dependencies=[Depends(guard)])
    async def start_bootstrap(join: bool = False) -> dict[str, Any]:
        if not state.store.session_names():
            raise HTTPException(409, "Нет сессий. Добавь: healdragg session add main")
        if state.running("boot"):
            raise HTTPException(409, "Сбор уже идёт")
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

    @app.get("/api/export/{tg_id}", dependencies=[Depends(guard)])
    async def export(tg_id: int) -> JSONResponse:
        target = state.store.get_target(tg_id)
        if not target:
            raise HTTPException(404, "Цель не найдена")
        return JSONResponse(state.store.export_target(int(target["id"])))

    # ── веб-панель ─────────────────────────────────────────────────────────
    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest() -> JSONResponse:
        return JSONResponse(
            {
                "name": "Healdragg",
                "short_name": "Healdragg",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#12121a",
                "theme_color": "#bd93f9",
                "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
            }
        )

    @app.get("/graph.js", include_in_schema=False)
    async def graph_js() -> FileResponse:
        return FileResponse(WEB_DIR / "graph.js", media_type="application/javascript")

    @app.get("/icon.svg", include_in_schema=False)
    async def icon() -> Any:
        from fastapi.responses import Response

        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect width="64" height="64" rx="16" fill="#12121a"/>'
            '<text x="32" y="44" font-size="36" text-anchor="middle">🩺</text></svg>'
        )
        return Response(svg, media_type="image/svg+xml")

    return app


def main() -> None:
    import uvicorn

    from .config import load_settings

    settings = load_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


__all__ = ["create_app", "main"]
