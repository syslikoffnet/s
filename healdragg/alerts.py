"""Алерты: цель зашла в новый чат, вышла из чата, написала сообщение.

Детекторы сравнивают базу с базовыми метками в ``meta`` и складывают события в
таблицу ``alerts``. Дальше их развозят бот (push), панель (колокольчик) и
CLI (``healdragg alerts``).
"""

from __future__ import annotations

import html
from typing import Any

from .db import Store
from .util import fmt_relative, now_ts, truncate

#: сколько алертов создавать за один прогон, чтобы не заспамить
MAX_PER_KIND = 12


def _baseline(store: Store, key: str) -> int:
    raw = store.get_meta(key)
    try:
        return int(raw or 0)
    except ValueError:
        return 0


def _set_baseline(store: Store, key: str, value: int) -> None:
    store.set_meta(key, str(int(value)))


def detect_new_chats(
    store: Store,
    target: dict[str, Any],
    *,
    since: int | None = None,
    limit: int = MAX_PER_KIND,
) -> list[dict[str, Any]]:
    """Чаты, в которые цель попала после прошлой проверки."""
    target_pk = int(target["id"])
    key = f"alert:chats:{target_pk}"
    baseline = _baseline(store, key)
    previous = max(baseline, int(since or 0))
    if not baseline:
        # первый прогон — просто запоминаем точку отсчёта, не спамим
        _set_baseline(store, key, max(int(since or 0), now_ts() - 1))
        return []

    rows = store.query(
        """
        SELECT g.id AS group_pk, g.title, g.tg_id, g.link, g.kind, g.is_public,
               g.members_count, tg.status, tg.found_at
          FROM target_groups tg JOIN groups g ON g.id = tg.group_id
         WHERE tg.target_id = ? AND tg.found_at > ?
         ORDER BY tg.found_at DESC LIMIT ?
        """,
        (target_pk, previous, int(limit)),
    )
    created: list[dict[str, Any]] = []
    for row in rows:
        alert_id = store.add_alert(
            "new_chat",
            f"🆕 Новый чат: {row['title'] or row['tg_id']}",
            body=(
                f"{row.get('kind') or 'чат'} · "
                f"{'публичный' if row.get('is_public') else 'приватный'} · "
                f"{row.get('members_count') or '?'} участников"
            ),
            target_pk=target_pk,
            dedup=f"chat:{target['tg_user_id']}:{row['tg_id']}",
            ts=int(row.get("found_at") or now_ts()),
        )
        if alert_id:
            created.append({**row, "id": alert_id, "kind": "new_chat"})
    _set_baseline(store, key, now_ts())
    return created


def detect_left_chats(
    store: Store,
    target: dict[str, Any],
    *,
    since: int | None = None,
    limit: int = MAX_PER_KIND,
) -> list[dict[str, Any]]:
    target_pk = int(target["id"])
    key = f"alert:left:{target_pk}"
    baseline = _baseline(store, key)
    previous = max(baseline, int(since or 0))
    if not baseline:
        _set_baseline(store, key, max(int(since or 0), now_ts() - 1))
        return []

    rows = store.query(
        """
        SELECT g.tg_id, g.title, tg.updated_at
          FROM target_groups tg JOIN groups g ON g.id = tg.group_id
         WHERE tg.target_id = ? AND tg.status = 'left' AND tg.updated_at > ?
         ORDER BY tg.updated_at DESC LIMIT ?
        """,
        (target_pk, previous, int(limit)),
    )
    created: list[dict[str, Any]] = []
    for row in rows:
        alert_id = store.add_alert(
            "left_chat",
            f"✖️ Вышел: {row['title'] or row['tg_id']}",
            target_pk=target_pk,
            dedup=f"left:{target['tg_user_id']}:{row['tg_id']}",
            ts=int(row.get("updated_at") or now_ts()),
        )
        if alert_id:
            created.append(
                {
                    **row,
                    "id": alert_id,
                    "kind": "left_chat",
                    "alert_title": f"✖️ Вышел: {row['title'] or row['tg_id']}",
                }
            )
    _set_baseline(store, key, now_ts())
    return created


def detect_new_messages(
    store: Store,
    target: dict[str, Any],
    *,
    limit: int = MAX_PER_KIND,
) -> list[dict[str, Any]]:
    target_pk = int(target["id"])
    key = f"alert:msg:{target_pk}"
    raw = store.get_meta(key)
    if raw is None:
        max_id = int(store.scalar("SELECT MAX(id) FROM messages WHERE target_id = ?", (target_pk,), 0))
        _set_baseline(store, key, max_id)
        return []
    last_id = int(raw or 0)

    rows = store.query(
        """
        SELECT m.id, m.text, m.date, m.media, g.title, g.link
          FROM messages m JOIN groups g ON g.id = m.group_id
         WHERE m.target_id = ? AND m.id > ?
         ORDER BY m.id LIMIT ?
        """,
        (target_pk, last_id, int(limit)),
    )
    created: list[dict[str, Any]] = []
    for row in rows:
        text = (row.get("text") or "").strip()
        title = row.get("title") or "чат"
        alert_id = store.add_alert(
            "message",
            f"💬 Написал в «{title}»",
            body=text[:300] if text else (row.get("media") or "без текста"),
            target_pk=target_pk,
            dedup=f"msg:{target['tg_user_id']}:{row['id']}",
            ts=int(row.get("date") or now_ts()),
        )
        if alert_id:
            created.append(
                {
                    **row,
                    "id": alert_id,
                    "kind": "message",
                    "alert_title": f"💬 Написал в «{row.get('title') or 'чат'}»",
                }
            )
    if rows:
        _set_baseline(store, key, int(rows[-1]["id"]))
    return created


def run_detection(
    store: Store,
    target: dict[str, Any],
    *,
    since: int | None = None,
    messages: bool = False,
) -> list[dict[str, Any]]:
    """Все детекторы разом — вызывается после скана/сбора активности."""
    created = detect_new_chats(store, target, since=since)
    created += detect_left_chats(store, target, since=since)
    if messages:
        created += detect_new_messages(store, target)
    return created


def format_alert(alert: dict[str, Any], *, tz: str = "Europe/Moscow") -> str:
    """HTML-строка для бота и панели."""
    lines = [f"<b>{html.escape(alert.get('title') or '')}</b>"]
    body = (alert.get("body") or "").strip()
    if body:
        lines.append(html.escape(truncate(body, 220)))
    meta: list[str] = []
    if alert.get("username"):
        meta.append("@" + alert["username"])
    elif alert.get("tg_user_id"):
        meta.append(str(alert["tg_user_id"]))
    if alert.get("ts"):
        meta.append(fmt_relative(alert["ts"], tz))
    if meta:
        lines.append("<i>" + " · ".join(meta) + "</i>")
    return "\n".join(lines)


def render_digest(alerts: list[dict[str, Any]], *, tz: str = "Europe/Moscow") -> str:
    head = "🔔 <b>Алерты</b>"
    if not alerts:
        return head + "\n\nПока тихо. Всё под контролем."
    return head + "\n\n" + "\n\n".join(format_alert(a, tz=tz) for a in alerts)


__all__ = [
    "detect_new_chats",
    "detect_left_chats",
    "detect_new_messages",
    "run_detection",
    "format_alert",
    "render_digest",
]
