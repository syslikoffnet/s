"""Общие зависимости бота."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..db import Store
from ..tg import SessionPool


@dataclass
class Job:
    task: asyncio.Task
    kind: str
    target_pk: int | None = None
    message_chat_id: int | None = None
    message_id: int | None = None
    started_at: float = 0.0


@dataclass
class Deps:
    settings: Settings
    store: Store
    pool: SessionPool
    jobs: dict[str, Job] = field(default_factory=dict)
    bot: Any = None

    def key(self, kind: str, ident: Any = "") -> str:
        return f"{kind}:{ident}"

    def running(self, key: str) -> bool:
        job = self.jobs.get(key)
        return bool(job and not job.task.done())

    async def cancel(self, key: str) -> bool:
        job = self.jobs.get(key)
        if not job or job.task.done():
            return False
        job.task.cancel()
        try:
            await job.task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        return True

    def register(self, key: str, task: asyncio.Task, **kwargs: Any) -> Job:
        job = Job(task=task, **kwargs)
        self.jobs[key] = job

        def _cleanup(_: asyncio.Task) -> None:
            self.jobs.pop(key, None)

        task.add_done_callback(_cleanup)
        return job

    @property
    def has_sessions(self) -> bool:
        return bool(self.store.session_names())
