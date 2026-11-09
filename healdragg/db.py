"""Хранилище Healdragg.

Одна SQLite-база, нормализованная схема, WAL и минимум избыточности:

* ``groups``          — каталог чатов (одна запись на чат, дедуп по ``tg_id``);
* ``sessions``        — аккаунты-сканеры, которые мы ведём;
* ``session_groups``  — в каких чатах состоит каждая сессия (+ access_hash);
* ``targets``         — за кем наблюдаем;
* ``target_groups``   — пересечение «цель × чат» (статус, источник, даты);
* ``scans``           — журнал прогонов, прогресс для TUI/бота/веба;
* ``events``          — короткий лог для панели (обрезается автоматически).

Сообщения НЕ сохраняются: только метаданные чатов. Это и приватнее, и в разы
компактнее — база на десятки тысяч групп занимает единицы мегабайт.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .util import group_link, now_ts

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    session_file TEXT NOT NULL,
    tg_user_id   INTEGER,
    username     TEXT,
    first_name   TEXT,
    phone        TEXT,
    status       TEXT NOT NULL DEFAULT 'new',
    last_error   TEXT,
    added_at     INTEGER NOT NULL,
    last_used_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS groups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id           INTEGER NOT NULL UNIQUE,
    access_hash     INTEGER,
    title           TEXT NOT NULL DEFAULT '',
    username        TEXT,
    link            TEXT,
    kind            TEXT NOT NULL DEFAULT 'group',
    is_public       INTEGER NOT NULL DEFAULT 0,
    is_megagroup    INTEGER NOT NULL DEFAULT 0,
    members_count   INTEGER,
    messages_total  INTEGER,
    last_message_at INTEGER,
    meta_checked_at INTEGER,
    discovered_from TEXT DEFAULT 'manual',
    discovered_by   TEXT,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_groups_username ON groups(username);
CREATE INDEX IF NOT EXISTS idx_groups_last     ON groups(last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_groups_public   ON groups(is_public, members_count DESC);
CREATE INDEX IF NOT EXISTS idx_groups_title    ON groups(title COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS session_groups (
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    group_id    INTEGER NOT NULL REFERENCES groups(id)  ON DELETE CASCADE,
    is_member   INTEGER NOT NULL DEFAULT 1,
    access_hash INTEGER,
    joined_at   INTEGER,
    checked_at  INTEGER,
    PRIMARY KEY (session_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_sg_group ON session_groups(group_id);
CREATE INDEX IF NOT EXISTS idx_sg_member ON session_groups(session_id, is_member);

CREATE TABLE IF NOT EXISTS targets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_user_id    INTEGER NOT NULL UNIQUE,
    access_hash   INTEGER,
    username      TEXT,
    first_name    TEXT,
    last_name     TEXT,
    phone         TEXT,
    bio           TEXT,
    is_bot        INTEGER NOT NULL DEFAULT 0,
    is_deleted    INTEGER NOT NULL DEFAULT 0,
    added_at      INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    last_scan_at  INTEGER,
    notes         TEXT
);
CREATE INDEX IF NOT EXISTS idx_targets_username ON targets(username);
CREATE INDEX IF NOT EXISTS idx_targets_updated  ON targets(updated_at DESC);

CREATE TABLE IF NOT EXISTS target_groups (
    target_id  INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    group_id   INTEGER NOT NULL REFERENCES groups(id)  ON DELETE CASCADE,
    status     TEXT NOT NULL DEFAULT 'member',
    source     TEXT NOT NULL DEFAULT 'probe',
    session    TEXT,
    found_at   INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (target_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_tg_target ON target_groups(target_id);
CREATE INDEX IF NOT EXISTS idx_tg_group  ON target_groups(group_id);
CREATE INDEX IF NOT EXISTS idx_tg_status ON target_groups(target_id, status);

CREATE TABLE IF NOT EXISTS scans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id   INTEGER,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',
    sessions    TEXT,
    total       INTEGER NOT NULL DEFAULT 0,
    done        INTEGER NOT NULL DEFAULT 0,
    found       INTEGER NOT NULL DEFAULT 0,
    started_at  INTEGER NOT NULL,
    finished_at INTEGER,
    stats       TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_scans_target ON scans(target_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_scans_status ON scans(status);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER NOT NULL,
    level   TEXT NOT NULL DEFAULT 'info',
    source  TEXT,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS groups_fts USING fts5(
    title, username, content='groups', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS groups_ai AFTER INSERT ON groups BEGIN
    INSERT INTO groups_fts(rowid, title, username) VALUES (new.id, new.title, coalesce(new.username, ''));
END;
CREATE TRIGGER IF NOT EXISTS groups_ad AFTER DELETE ON groups BEGIN
    INSERT INTO groups_fts(groups_fts, rowid, title, username) VALUES ('delete', old.id, old.title, coalesce(old.username, ''));
END;
CREATE TRIGGER IF NOT EXISTS groups_au AFTER UPDATE OF title, username ON groups BEGIN
    INSERT INTO groups_fts(groups_fts, rowid, title, username) VALUES ('delete', old.id, old.title, coalesce(old.username, ''));
    INSERT INTO groups_fts(rowid, title, username) VALUES (new.id, new.title, coalesce(new.username, ''));
END;
"""

GROUP_FIELDS = (
    "tg_id",
    "access_hash",
    "title",
    "username",
    "link",
    "kind",
    "is_public",
    "is_megagroup",
    "members_count",
    "messages_total",
    "last_message_at",
    "meta_checked_at",
    "discovered_from",
    "discovered_by",
)

TARGET_GROUP_SELECT = """
SELECT tg.target_id, tg.group_id, tg.status, tg.source, tg.session,
       tg.found_at, tg.updated_at,
       g.tg_id, g.title, g.username, g.link, g.kind, g.is_public, g.members_count,
       g.messages_total, g.last_message_at
  FROM target_groups tg
  JOIN groups g ON g.id = tg.group_id
"""


class Store:
    """Тонкая, но аккуратная обёртка над sqlite3 с потокобезопасным доступом."""

    def __init__(self, path: str | Path, *, timeout: float = 10.0) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), timeout=timeout, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self._migrate()

    # ── инфраструктура ──────────────────────────────────────────────────────
    def _configure(self) -> None:
        for pragma in (
            "PRAGMA journal_mode=WAL",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA foreign_keys=ON",
            "PRAGMA busy_timeout=8000",
            "PRAGMA temp_store=MEMORY",
            "PRAGMA cache_size=-16000",
            "PRAGMA mmap_size=134217728",
            "PRAGMA auto_vacuum=INCREMENTAL",
            "PRAGMA journal_size_limit=2097152",
            "PRAGMA wal_autocheckpoint=400",
        ):
            try:
                self._conn.execute(pragma)
            except sqlite3.DatabaseError:
                pass

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            try:
                self._conn.executescript(FTS_SCHEMA)
                self._fts = True
            except sqlite3.DatabaseError:
                self._fts = False
            self._migrate_columns()
            version = self.get_meta("schema_version")
            if version is None:
                self.set_meta("schema_version", str(SCHEMA_VERSION))
                self.set_meta("created_at", str(now_ts()))
            self._conn.commit()

    def _migrate_columns(self) -> None:
        """Догоняющие ALTER TABLE для баз, созданных старыми версиями."""
        cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(groups)")}
        if "link" not in cols:
            self._conn.execute("ALTER TABLE groups ADD COLUMN link TEXT")
        # ссылки для уже собранных чатов: публичные — по username,
        # приватные супергруппы/каналы — рабочая t.me/c/… для участников
        self._conn.execute(
            "UPDATE groups SET link = 'https://t.me/' || username "
            "WHERE link IS NULL AND username IS NOT NULL AND username <> ''"
        )
        self._conn.execute(
            "UPDATE groups SET link = 'https://t.me/c/' || "
            "(CASE WHEN tg_id < 0 THEN -tg_id - 1000000000000 ELSE tg_id END) || '/1' "
            "WHERE link IS NULL AND kind IN ('channel', 'supergroup') AND tg_id <> 0"
        )
        self._conn.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
            except sqlite3.Error:
                pass
            self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = 0) -> Any:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        if not row or row[0] is None:
            return default
        return row[0]

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        with self.tx() as conn:
            conn.executemany(sql, seq)

    # ── meta ────────────────────────────────────────────────────────────────
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.query_one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ── счётчики (дневные лимиты) ───────────────────────────────────────────
    @staticmethod
    def _day_key(key: str, day: str | None = None) -> str:
        stamp = day or time.strftime("%Y-%m-%d")
        return f"counter:{key}:{stamp}"

    def daily_count(self, key: str, day: str | None = None) -> int:
        raw = self.get_meta(self._day_key(key, day), "0") or "0"
        try:
            return int(raw)
        except ValueError:
            return 0

    def incr_daily(self, key: str, amount: int = 1, day: str | None = None) -> int:
        meta_key = self._day_key(key, day)
        value = self.daily_count(key, day) + amount
        self.set_meta(meta_key, str(value))
        return value

    # ── сессии ──────────────────────────────────────────────────────────────
    def add_session(
        self,
        name: str,
        session_file: str,
        *,
        tg_user_id: int | None = None,
        username: str | None = None,
        first_name: str | None = None,
        phone: str | None = None,
        status: str = "active",
    ) -> int:
        with self.tx() as conn:
            conn.execute(
                """
                INSERT INTO sessions(name, session_file, tg_user_id, username, first_name, phone, status, added_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                    session_file = excluded.session_file,
                    tg_user_id   = coalesce(excluded.tg_user_id, sessions.tg_user_id),
                    username     = coalesce(excluded.username, sessions.username),
                    first_name   = coalesce(excluded.first_name, sessions.first_name),
                    phone        = coalesce(excluded.phone, sessions.phone),
                    status       = excluded.status
                """,
                (name, session_file, tg_user_id, username, first_name, phone, status, now_ts()),
            )
            row = conn.execute("SELECT id FROM sessions WHERE name = ?", (name,)).fetchone()
        return int(row["id"])

    def set_session_status(self, name: str, status: str, error: str | None = None) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE sessions SET status = ?, last_error = ? WHERE name = ?",
                (status, error, name),
            )

    def touch_session(self, name: str) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE sessions SET last_used_at = ? WHERE name = ?", (now_ts(), name))

    def list_sessions(self) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT s.*, (SELECT COUNT(*) FROM session_groups sg WHERE sg.session_id = s.id AND sg.is_member = 1)
                   AS groups_count
              FROM sessions s ORDER BY s.id
            """
        )

    def get_session(self, name: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM sessions WHERE name = ?", (name,))

    def session_names(self, only_active: bool = True) -> list[str]:
        if only_active:
            rows = self.query("SELECT name FROM sessions WHERE status = 'active' ORDER BY id")
        else:
            rows = self.query("SELECT name FROM sessions ORDER BY id")
        return [r["name"] for r in rows]

    def remove_session(self, name: str) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM sessions WHERE name = ?", (name,))

    # ── группы ──────────────────────────────────────────────────────────────
    def upsert_group(
        self,
        tg_id: int,
        *,
        title: str = "",
        access_hash: int | None = None,
        username: str | None = None,
        link: str | None = None,
        kind: str = "group",
        is_public: bool | None = None,
        is_megagroup: bool = False,
        members_count: int | None = None,
        messages_total: int | None = None,
        last_message_at: int | None = None,
        meta_checked_at: int | None = None,
        discovered_from: str | None = None,
        discovered_by: str | None = None,
    ) -> int:
        if is_public is None:
            is_public = bool(username)
        if link is None:
            link = group_link(tg_id, username, kind)
        ts = now_ts()
        with self.tx() as conn:
            conn.execute(
                """
                INSERT INTO groups(tg_id, access_hash, title, username, link, kind, is_public, is_megagroup,
                                   members_count, messages_total, last_message_at, meta_checked_at,
                                   discovered_from, discovered_by, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(tg_id) DO UPDATE SET
                    access_hash     = coalesce(excluded.access_hash, groups.access_hash),
                    title           = CASE WHEN excluded.title <> '' THEN excluded.title ELSE groups.title END,
                    username        = coalesce(excluded.username, groups.username),
                    link            = CASE
                        WHEN excluded.username IS NOT NULL THEN 'https://t.me/' || excluded.username
                        WHEN groups.username IS NOT NULL THEN coalesce(groups.link, excluded.link)
                        ELSE coalesce(excluded.link, groups.link)
                    END,
                    kind            = excluded.kind,
                    is_public       = CASE WHEN excluded.username IS NOT NULL THEN 1 ELSE groups.is_public END,
                    is_megagroup    = excluded.is_megagroup OR groups.is_megagroup,
                    members_count   = coalesce(excluded.members_count, groups.members_count),
                    messages_total  = coalesce(excluded.messages_total, groups.messages_total),
                    last_message_at = coalesce(excluded.last_message_at, groups.last_message_at),
                    meta_checked_at = coalesce(excluded.meta_checked_at, groups.meta_checked_at),
                    discovered_from = coalesce(groups.discovered_from, excluded.discovered_from),
                    discovered_by   = coalesce(groups.discovered_by, excluded.discovered_by),
                    updated_at      = excluded.updated_at
                """,
                (
                    tg_id,
                    access_hash,
                    title or "",
                    username,
                    link,
                    kind,
                    int(bool(is_public)),
                    int(bool(is_megagroup)),
                    members_count,
                    messages_total,
                    last_message_at,
                    meta_checked_at,
                    discovered_from,
                    discovered_by,
                    ts,
                    ts,
                ),
            )
            row = conn.execute("SELECT id FROM groups WHERE tg_id = ?", (tg_id,)).fetchone()
        return int(row["id"])

    def get_group(self, tg_id: int) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM groups WHERE tg_id = ?", (tg_id,))

    def get_group_by_pk(self, group_id: int) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM groups WHERE id = ?", (group_id,))

    def groups_by_tg_ids(self, tg_ids: Iterable[int]) -> list[dict[str, Any]]:
        ids = [int(i) for i in tg_ids]
        out: list[dict[str, Any]] = []
        for start in range(0, len(ids), 500):
            part = ids[start : start + 500]
            if not part:
                continue
            placeholders = ",".join("?" for _ in part)
            out.extend(
                self.query(f"SELECT * FROM groups WHERE tg_id IN ({placeholders})", part)
            )
        return out

    def count_groups(self, query: str | None = None, public_only: bool = False) -> int:
        where, params = self._group_where(query, public_only)
        return int(self.scalar(f"SELECT COUNT(*) FROM groups {where}", params, 0))

    def _group_where(self, query: str | None, public_only: bool) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if public_only:
            clauses.append("is_public = 1")
        if query:
            q = query.strip().lstrip("@")
            if q:
                clauses.append("(title LIKE ? OR username LIKE ?)")
                params.extend([f"%{q}%", f"%{q}%"])
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", params

    def list_groups(
        self,
        *,
        page: int = 1,
        size: int = 50,
        query: str | None = None,
        public_only: bool = False,
        order: str = "last",
    ) -> list[dict[str, Any]]:
        where, params = self._group_where(query, public_only)
        order_sql = {
            "last": "last_message_at DESC NULLS LAST, members_count DESC",
            "members": "members_count DESC NULLS LAST, title",
            "title": "title COLLATE NOCASE",
            "new": "created_at DESC",
        }.get(order, "last_message_at DESC NULLS LAST")
        offset = max(0, (page - 1) * size)
        return self.query(
            f"SELECT * FROM groups {where} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            [*params, size, offset],
        )

    def search_groups(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        q = (query or "").strip().lstrip("@")
        if not q:
            return []
        if getattr(self, "_fts", False):
            try:
                rows = self.query(
                    """
                    SELECT g.* FROM groups_fts f JOIN groups g ON g.id = f.rowid
                     WHERE groups_fts MATCH ? ORDER BY rank LIMIT ?
                    """,
                    [q.replace('"', "") + "*", limit],
                )
                if rows:
                    return rows
            except sqlite3.DatabaseError:
                pass
        return self.query(
            "SELECT * FROM groups WHERE title LIKE ? OR username LIKE ? "
            "ORDER BY members_count DESC NULLS LAST LIMIT ?",
            (f"%{q}%", f"%{q}%", limit),
        )

    def delete_group(self, tg_id: int) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM groups WHERE tg_id = ?", (tg_id,))

    def groups_needing_meta(self, limit: int = 500, ttl_hours: float = 12.0) -> list[dict[str, Any]]:
        cutoff = now_ts() - int(ttl_hours * 3600)
        return self.query(
            "SELECT * FROM groups WHERE coalesce(meta_checked_at, 0) < ? "
            "ORDER BY coalesce(last_message_at, 0) DESC LIMIT ?",
            (cutoff, limit),
        )

    def set_group_meta(
        self,
        tg_id: int,
        *,
        members_count: int | None = None,
        messages_total: int | None = None,
        last_message_at: int | None = None,
        title: str | None = None,
        username: str | None = None,
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                """
                UPDATE groups SET
                    members_count   = coalesce(?, members_count),
                    messages_total  = coalesce(?, messages_total),
                    last_message_at = coalesce(?, last_message_at),
                    title           = coalesce(?, title),
                    username        = coalesce(?, username),
                    meta_checked_at = ?,
                    updated_at      = ?
                WHERE tg_id = ?
                """,
                (members_count, messages_total, last_message_at, title, username, now_ts(), now_ts(), tg_id),
            )

    # ── сессия ↔ группы ────────────────────────────────────────────────────
    def link_session_group(
        self,
        session_id: int,
        group_id: int,
        *,
        is_member: bool = True,
        access_hash: int | None = None,
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                """
                INSERT INTO session_groups(session_id, group_id, is_member, access_hash, joined_at, checked_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(session_id, group_id) DO UPDATE SET
                    is_member   = excluded.is_member,
                    access_hash = coalesce(excluded.access_hash, session_groups.access_hash),
                    checked_at  = excluded.checked_at
                """,
                (session_id, group_id, int(bool(is_member)), access_hash, now_ts(), now_ts()),
            )

    def session_groups(self, session_name: str, members_only: bool = True) -> list[dict[str, Any]]:
        sql = """
            SELECT g.*, sg.access_hash AS session_access_hash, sg.is_member
              FROM session_groups sg
              JOIN sessions s ON s.id = sg.session_id
              JOIN groups g   ON g.id = sg.group_id
             WHERE s.name = ?
        """
        if members_only:
            sql += " AND sg.is_member = 1"
        sql += " ORDER BY coalesce(g.last_message_at, 0) DESC"
        return self.query(sql, (session_name,))

    def session_group_map(self, session_names: Sequence[str] | None = None) -> dict[str, dict[int, Any]]:
        """``{session: {tg_id: access_hash}}`` — карта покрытия для проверок."""
        sql = """
            SELECT s.name AS session_name, g.tg_id, sg.access_hash
              FROM session_groups sg
              JOIN sessions s ON s.id = sg.session_id
              JOIN groups g   ON g.id = sg.group_id
             WHERE sg.is_member = 1
        """
        params: list[Any] = []
        if session_names:
            placeholders = ",".join("?" for _ in session_names)
            sql += f" AND s.name IN ({placeholders})"
            params.extend(session_names)
        out: dict[str, dict[int, Any]] = {}
        for row in self.query(sql, params):
            out.setdefault(row["session_name"], {})[row["tg_id"]] = row["access_hash"]
        return out

    def coverage(self) -> dict[str, int]:
        row = self.query_one(
            """
            SELECT COUNT(DISTINCT group_id) AS groups,
                   COUNT(DISTINCT session_id) AS sessions,
                   COUNT(*) AS pairs
              FROM session_groups WHERE is_member = 1
            """
        ) or {}
        return {k: int(v or 0) for k, v in row.items()}

    # ── цели ────────────────────────────────────────────────────────────────
    def upsert_target(
        self,
        tg_user_id: int,
        *,
        access_hash: int | None = None,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        phone: str | None = None,
        bio: str | None = None,
        is_bot: bool = False,
        is_deleted: bool = False,
        notes: str | None = None,
    ) -> dict[str, Any]:
        ts = now_ts()
        with self.tx() as conn:
            conn.execute(
                """
                INSERT INTO targets(tg_user_id, access_hash, username, first_name, last_name, phone,
                                    bio, is_bot, is_deleted, added_at, updated_at, notes)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(tg_user_id) DO UPDATE SET
                    access_hash = coalesce(excluded.access_hash, targets.access_hash),
                    username    = coalesce(excluded.username, targets.username),
                    first_name  = coalesce(excluded.first_name, targets.first_name),
                    last_name   = coalesce(excluded.last_name, targets.last_name),
                    phone       = coalesce(excluded.phone, targets.phone),
                    bio         = coalesce(excluded.bio, targets.bio),
                    is_bot      = excluded.is_bot,
                    is_deleted  = excluded.is_deleted,
                    notes       = coalesce(excluded.notes, targets.notes),
                    updated_at  = excluded.updated_at
                """,
                (
                    tg_user_id,
                    access_hash,
                    username,
                    first_name,
                    last_name,
                    phone,
                    bio,
                    int(is_bot),
                    int(is_deleted),
                    ts,
                    ts,
                    notes,
                ),
            )
            row = conn.execute("SELECT * FROM targets WHERE tg_user_id = ?", (tg_user_id,)).fetchone()
        return dict(row)

    def get_target(self, tg_user_id: int) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM targets WHERE tg_user_id = ?", (tg_user_id,))

    def find_target(self, query: str) -> dict[str, Any] | None:
        q = (query or "").strip()
        if not q:
            return None
        if q.lstrip("@").isdigit():
            return self.get_target(int(q.lstrip("@")))
        return self.query_one(
            "SELECT * FROM targets WHERE lower(username) = lower(?) ORDER BY updated_at DESC LIMIT 1",
            (q.lstrip("@"),),
        )

    def list_targets(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT t.*, (SELECT COUNT(*) FROM target_groups tg WHERE tg.target_id = t.id) AS groups_count
              FROM targets t ORDER BY t.updated_at DESC LIMIT ?
            """,
            (limit,),
        )

    def count_targets(self) -> int:
        return int(self.scalar("SELECT COUNT(*) FROM targets", (), 0))

    def delete_target(self, tg_user_id: int) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM targets WHERE tg_user_id = ?", (tg_user_id,))

    def touch_target_scan(self, tg_user_id: int) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE targets SET last_scan_at = ? WHERE tg_user_id = ?", (now_ts(), tg_user_id))

    # ── цель ↔ группы ──────────────────────────────────────────────────────
    def upsert_target_group(
        self,
        target_pk: int,
        group_pk: int,
        *,
        status: str = "member",
        source: str = "probe",
        session: str | None = None,
    ) -> None:
        ts = now_ts()
        with self.tx() as conn:
            conn.execute(
                """
                INSERT INTO target_groups(target_id, group_id, status, source, session, found_at, updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(target_id, group_id) DO UPDATE SET
                    status     = excluded.status,
                    source     = excluded.source,
                    session    = coalesce(excluded.session, target_groups.session),
                    updated_at = excluded.updated_at
                """,
                (target_pk, group_pk, status, source, session, ts, ts),
            )

    def mark_target_group(self, target_pk: int, group_pk: int, status: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE target_groups SET status = ?, updated_at = ? WHERE target_id = ? AND group_id = ?",
                (status, now_ts(), target_pk, group_pk),
            )

    def mark_target_groups_left(
        self,
        target_pk: int,
        keep_group_ids: Iterable[int],
        *,
        source: str | None = None,
    ) -> int:
        """Помечает связи «цель × чат» как ``left``, если чата больше нет в списке.

        Используется при скане «своей» сессии: если раньше чат был, а теперь его
        нет в диалогах — значит аккаунт вышел (или чат удалён).
        """
        keep = {int(i) for i in keep_group_ids}
        rows = self.query(
            "SELECT group_id, status, source FROM target_groups WHERE target_id = ?",
            (target_pk,),
        )
        missing = [
            int(r["group_id"])
            for r in rows
            if int(r["group_id"]) not in keep
            and r["status"] != "left"
            and (source is None or r["source"] == source)
        ]
        if not missing:
            return 0
        ts = now_ts()
        with self.tx() as conn:
            for start in range(0, len(missing), 500):
                part = missing[start : start + 500]
                placeholders = ",".join("?" for _ in part)
                conn.execute(
                    f"UPDATE target_groups SET status = 'left', updated_at = ? "
                    f"WHERE target_id = ? AND group_id IN ({placeholders})",
                    [ts, target_pk, *part],
                )
        return len(missing)

    def count_target_groups(self, target_pk: int, status: str | None = None) -> int:
        if status:
            return int(
                self.scalar(
                    "SELECT COUNT(*) FROM target_groups WHERE target_id = ? AND status = ?",
                    (target_pk, status),
                    0,
                )
            )
        return int(self.scalar("SELECT COUNT(*) FROM target_groups WHERE target_id = ?", (target_pk,), 0))

    def list_target_groups(
        self,
        target_pk: int,
        *,
        page: int = 1,
        size: int = 15,
        order: str = "last",
    ) -> list[dict[str, Any]]:
        order_sql = {
            "last": "coalesce(g.last_message_at, 0) DESC, g.title COLLATE NOCASE",
            "members": "coalesce(g.members_count, 0) DESC, g.title COLLATE NOCASE",
            "title": "g.title COLLATE NOCASE",
            "found": "tg.found_at DESC",
        }.get(order, "coalesce(g.last_message_at, 0) DESC")
        offset = max(0, (page - 1) * size)
        return self.query(
            TARGET_GROUP_SELECT
            + f" WHERE tg.target_id = ? ORDER BY {order_sql} LIMIT ? OFFSET ?",
            (target_pk, size, offset),
        )

    def all_target_groups(self, target_pk: int) -> list[dict[str, Any]]:
        return self.query(
            TARGET_GROUP_SELECT
            + " WHERE tg.target_id = ? ORDER BY coalesce(g.last_message_at, 0) DESC",
            (target_pk,),
        )

    def target_stats(self, target_pk: int) -> dict[str, Any]:
        base = self.query_one(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status IN ('admin','creator') THEN 1 ELSE 0 END) AS admin,
                   SUM(CASE WHEN status = 'left' THEN 1 ELSE 0 END) AS left_count,
                   SUM(CASE WHEN g.is_public = 0 THEN 1 ELSE 0 END) AS private,
                   MAX(tg.updated_at) AS updated_at
              FROM target_groups tg JOIN groups g ON g.id = tg.group_id
             WHERE tg.target_id = ?
            """,
            (target_pk,),
        ) or {}
        out = {k: (int(v) if v is not None else 0) for k, v in base.items()}
        out["public"] = max(0, out.get("total", 0) - out.get("private", 0))
        return out

    def group_activity(self, group_pk: int) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM groups WHERE id = ?", (group_pk,))

    # ── прогоны ────────────────────────────────────────────────────────────
    def start_scan(
        self,
        kind: str,
        *,
        target_pk: int | None = None,
        sessions: Sequence[str] = (),
        total: int = 0,
    ) -> int:
        with self.tx() as conn:
            cur = conn.execute(
                "INSERT INTO scans(target_id, kind, status, sessions, total, started_at) VALUES(?,?,?,?,?,?)",
                (
                    target_pk,
                    kind,
                    "running",
                    json.dumps(list(sessions), ensure_ascii=False),
                    total,
                    now_ts(),
                ),
            )
            return int(cur.lastrowid)

    def update_scan(self, scan_id: int, **fields: Any) -> None:
        allowed = {"total", "done", "found", "status", "error", "stats"}
        sets, params = [], []
        for key, value in fields.items():
            if key in allowed:
                sets.append(f"{key} = ?")
                params.append(json.dumps(value, ensure_ascii=False) if key == "stats" else value)
        if not sets:
            return
        params.append(scan_id)
        with self.tx() as conn:
            conn.execute(f"UPDATE scans SET {', '.join(sets)} WHERE id = ?", params)

    def finish_scan(
        self,
        scan_id: int,
        *,
        status: str = "done",
        stats: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE scans SET status = ?, finished_at = ?, stats = ?, error = ? WHERE id = ?",
                (
                    status,
                    now_ts(),
                    json.dumps(stats or {}, ensure_ascii=False),
                    error,
                    scan_id,
                ),
            )

    def get_scan(self, scan_id: int) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM scans WHERE id = ?", (scan_id,))

    def last_scan(self, target_pk: int | None = None) -> dict[str, Any] | None:
        if target_pk is None:
            return self.query_one("SELECT * FROM scans ORDER BY id DESC LIMIT 1")
        return self.query_one(
            "SELECT * FROM scans WHERE target_id = ? ORDER BY id DESC LIMIT 1", (target_pk,)
        )

    def running_scans(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM scans WHERE status = 'running' ORDER BY id")

    def cancel_stale_scans(self, older_than: int = 900) -> int:
        cutoff = now_ts() - older_than
        with self.tx() as conn:
            cur = conn.execute(
                "UPDATE scans SET status = 'error', error = 'interrupted', finished_at = ? "
                "WHERE status = 'running' AND started_at < ?",
                (now_ts(), cutoff),
            )
            return cur.rowcount or 0

    # ── события ────────────────────────────────────────────────────────────
    def log_event(self, level: str, message: str, source: str | None = None) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO events(ts, level, source, message) VALUES(?,?,?,?)",
                (now_ts(), level, source, message[:2000]),
            )
            conn.execute(
                "DELETE FROM events WHERE id <= (SELECT max(id) - 500 FROM events)"
            )

    def recent_events(self, limit: int = 12) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return list(reversed(rows))

    # ── статистика и обслуживание ──────────────────────────────────────────
    def stats(self) -> dict[str, Any]:
        groups = self.query_one(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN is_public = 1 THEN 1 ELSE 0 END) AS public,
                   SUM(CASE WHEN username IS NOT NULL THEN 1 ELSE 0 END) AS with_username,
                   SUM(CASE WHEN last_message_at > 0 THEN 1 ELSE 0 END) AS active,
                   SUM(CASE WHEN meta_checked_at IS NOT NULL THEN 1 ELSE 0 END) AS with_meta,
                   MAX(updated_at) AS updated_at
              FROM groups
            """
        ) or {}
        counts = {
            "groups": int(groups.get("total") or 0),
            "public_groups": int(groups.get("public") or 0),
            "with_username": int(groups.get("with_username") or 0),
            "active_groups": int(groups.get("active") or 0),
            "with_meta": int(groups.get("with_meta") or 0),
            "targets": self.count_targets(),
            "target_groups": int(self.scalar("SELECT COUNT(*) FROM target_groups", (), 0)),
            "sessions": int(self.scalar("SELECT COUNT(*) FROM sessions", (), 0)),
            "scans": int(self.scalar("SELECT COUNT(*) FROM scans", (), 0)),
            "db_bytes": self.size_bytes(),
            "updated_at": groups.get("updated_at") or 0,
        }
        cov = self.coverage()
        counts["coverage_groups"] = cov["groups"]
        counts["covered_sessions"] = cov["sessions"]
        counts["pairs"] = cov["pairs"]
        return counts

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    def optimize(self) -> None:
        with self._lock:
            for stmt in (
                    "ANALYZE",
                "PRAGMA incremental_vacuum",
                "PRAGMA optimize",
            ):
                try:
                    self._conn.execute(stmt)
                except sqlite3.DatabaseError:
                    pass
            self._conn.commit()

    def vacuum(self) -> None:
        with self._lock:
            self._conn.commit()
            self._conn.execute("VACUUM")
            self._conn.commit()

    def integrity_check(self) -> str:
        return str(self.scalar("PRAGMA integrity_check", (), "unknown"))

    # ── экспорт / импорт ───────────────────────────────────────────────────
    def export_target(self, target_pk: int) -> dict[str, Any]:
        target = self.query_one("SELECT * FROM targets WHERE id = ?", (target_pk,))
        if not target:
            raise KeyError(target_pk)
        rows = self.all_target_groups(target_pk)
        return {
            "version": 1,
            "exported_at": now_ts(),
            "target": target,
            "groups": [
                {
                    "tg_id": r["tg_id"],
                    "title": r["title"],
                    "username": r["username"],
                    "link": r.get("link"),
                    "kind": r["kind"],
                    "is_public": r["is_public"],
                    "members_count": r["members_count"],
                    "messages_total": r["messages_total"],
                    "last_message_at": r["last_message_at"],
                    "status": r["status"],
                    "source": r["source"],
                }
                for r in rows
            ],
        }

    def import_target(self, payload: dict[str, Any]) -> int:
        target = payload.get("target") or {}
        tg_user_id = int(target.get("tg_user_id"))
        row = self.upsert_target(
            tg_user_id,
            username=target.get("username"),
            first_name=target.get("first_name"),
            last_name=target.get("last_name"),
            phone=target.get("phone"),
        )
        for group in payload.get("groups", []):
            gid = self.upsert_group(
                int(group["tg_id"]),
                title=group.get("title") or "",
                username=group.get("username"),
                link=group.get("link"),
                kind=group.get("kind") or "group",
                is_public=bool(group.get("is_public")),
                members_count=group.get("members_count"),
                messages_total=group.get("messages_total"),
                last_message_at=group.get("last_message_at"),
                discovered_from="import",
            )
            self.upsert_target_group(
                int(row["id"]),
                gid,
                status=group.get("status") or "member",
                source=group.get("source") or "import",
            )
        return int(row["id"])

    def table_counts(self) -> dict[str, int]:
        tables = ("sessions", "groups", "session_groups", "targets", "target_groups", "scans", "events")
        return {t: int(self.scalar(f"SELECT COUNT(*) FROM {t}", (), 0)) for t in tables}


def open_store(path: str | Path) -> Store:
    return Store(path)


__all__ = ["Store", "open_store", "SCHEMA_VERSION"]
