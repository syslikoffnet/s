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
import logging
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .util import group_link, now_ts

log = logging.getLogger("healdragg.db")

SCHEMA_VERSION = 9

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

CREATE TABLE IF NOT EXISTS peers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id      INTEGER NOT NULL UNIQUE,
    username   TEXT,
    first_name TEXT,
    last_name  TEXT,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_peers_username ON peers(username);

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id     INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    group_id      INTEGER NOT NULL REFERENCES groups(id)  ON DELETE CASCADE,
    tg_msg_id     INTEGER NOT NULL,
    date          INTEGER NOT NULL,
    text          TEXT NOT NULL DEFAULT '',
    reply_to_msg  INTEGER,
    reply_to_peer INTEGER,
    mentions      TEXT,
    media         TEXT,
    views         INTEGER,
    collected_at  INTEGER NOT NULL,
    UNIQUE(target_id, group_id, tg_msg_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_target ON messages(target_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_messages_group  ON messages(group_id);

CREATE TABLE IF NOT EXISTS edges (
    target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    peer_id   INTEGER NOT NULL REFERENCES peers(id)   ON DELETE CASCADE,
    weight    INTEGER NOT NULL DEFAULT 0,
    replies   INTEGER NOT NULL DEFAULT 0,
    mentions  INTEGER NOT NULL DEFAULT 0,
    last_seen INTEGER,
    PRIMARY KEY (target_id, peer_id)
);
CREATE INDEX IF NOT EXISTS idx_edges_weight ON edges(target_id, weight DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        INTEGER NOT NULL,
    target_id INTEGER REFERENCES targets(id) ON DELETE CASCADE,
    kind      TEXT NOT NULL,
    title     TEXT NOT NULL,
    body      TEXT,
    dedup     TEXT UNIQUE,
    read      INTEGER NOT NULL DEFAULT 0,
    sent_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts     ON alerts(ts DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_unread ON alerts(read, ts DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_unsent ON alerts(sent_at, ts);


CREATE TABLE IF NOT EXISTS gifts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_tg_id   INTEGER NOT NULL,
    saved_id      INTEGER,
    gift_id       INTEGER,
    title         TEXT,
    kind          TEXT NOT NULL DEFAULT 'regular',
    stars         INTEGER,
    from_tg_id    INTEGER,
    from_username TEXT,
    from_name     TEXT,
    owner_username TEXT,
    owner_name     TEXT,
    message       TEXT,
    date          INTEGER,
    is_nft        INTEGER NOT NULL DEFAULT 0,
    name_hidden   INTEGER NOT NULL DEFAULT 0,
    slug          TEXT,
    num           INTEGER,
    value_amount  INTEGER,
    value_currency TEXT,
    resell_amount INTEGER,
    availability_issued INTEGER,
    availability_total  INTEGER,
    on_chain      INTEGER NOT NULL DEFAULT 0,
    gift_address  TEXT,
    owner_address TEXT,
    value_usd     INTEGER,
    burned        INTEGER NOT NULL DEFAULT 0,
    crafted       INTEGER,
    raw           TEXT,
    seen_at       INTEGER NOT NULL,
    UNIQUE(owner_tg_id, saved_id, gift_id)
);
CREATE INDEX IF NOT EXISTS idx_gifts_owner ON gifts(owner_tg_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_gifts_from  ON gifts(from_tg_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_gifts_nft   ON gifts(is_nft, date DESC);
CREATE INDEX IF NOT EXISTS idx_gifts_chain ON gifts(on_chain, date DESC);

-- Отложенные тяжёлые профили и история наблюдений владельцев NFT.
-- Очередь обхода сохраняется независимо от процесса и экрана.
-- Ссылка на оригинальный стикер Telegram; сам файл загружается по требованию.
CREATE TABLE IF NOT EXISTS gift_stickers (
    gift_id INTEGER PRIMARY KEY, document BLOB NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS gift_crawl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT NOT NULL,
    depth INTEGER NOT NULL, max_profiles INTEGER NOT NULL, per_profile INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'running', initialized INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS gift_crawl_queue (
    run_id INTEGER NOT NULL REFERENCES gift_crawl_runs(id) ON DELETE CASCADE,
    tg_id INTEGER NOT NULL, username TEXT, access_hash INTEGER, level INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending', error TEXT,
    PRIMARY KEY(run_id, tg_id)
);
CREATE INDEX IF NOT EXISTS idx_gift_queue_pending ON gift_crawl_queue(run_id, state, level);

CREATE TABLE IF NOT EXISTS gift_profile_state (
    owner_tg_id INTEGER PRIMARY KEY, completed_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS gift_heavy_profiles (
    owner_tg_id INTEGER PRIMARY KEY, username TEXT, name TEXT,
    gift_count INTEGER NOT NULL, detected_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS nft_owner_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL, owner_tg_id INTEGER NOT NULL,
    owner_username TEXT, owner_name TEXT, observed_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nft_history_slug ON nft_owner_history(slug, id);

-- NFT-инвентарь: каждая уникальная вещь ровно один раз (slug = <коллекция>-<num>).
CREATE TABLE IF NOT EXISTS nft_items (
    slug          TEXT PRIMARY KEY,
    gift_id       INTEGER,
    num           INTEGER,
    title         TEXT,
    owner_tg_id   INTEGER,
    owner_username TEXT,
    owner_name    TEXT,
    owner_address TEXT,
    gift_address  TEXT,
    on_chain      INTEGER NOT NULL DEFAULT 0,
    burned        INTEGER NOT NULL DEFAULT 0,
    crafted       INTEGER,
    value_amount  INTEGER,
    value_currency TEXT,
    value_usd     INTEGER,
    resell_amount INTEGER,
    availability_issued INTEGER,
    availability_total  INTEGER,
    seen_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nft_chain  ON nft_items(on_chain, seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_nft_owner  ON nft_items(owner_tg_id, seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_nft_gift   ON nft_items(gift_id);

-- Коллекции уникальных подарков: перебираем slug'и по номерам и добираем владельцев.
CREATE TABLE IF NOT EXISTS nft_collections (
    slug_base     TEXT PRIMARY KEY,
    gift_id       INTEGER,
    title         TEXT,
    total         INTEGER NOT NULL DEFAULT 0,
    cursor        INTEGER NOT NULL DEFAULT 1,
    scanned       INTEGER NOT NULL DEFAULT 0,
    owners        INTEGER NOT NULL DEFAULT 0,
    on_chain      INTEGER NOT NULL DEFAULT 0,
    errors        INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    updated_at    INTEGER NOT NULL
);

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


def _is_comment_only(statement: str) -> bool:
    for line in statement.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return False
    return True


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
            # Порядок важен: сначала таблицы и всё, что к ним липнет, потом
            # догоняющие ALTER TABLE, и только потом — то, что не создалось
            # из-за отсутствующих колонок (индексы старой базы).
            pending = self._apply_script(SCHEMA)
            try:
                self._migrate_columns()
            except sqlite3.DatabaseError:
                # Старая база бывает совсем нестандартной: лучше подняться
                # на том, что есть, чем не запустить движок вообще.
                log.exception("догоняющая миграция не удалсь — продолжаю как есть")
            for statement in pending:
                try:
                    self._conn.execute(statement)
                except sqlite3.DatabaseError:
                    pass
            try:
                self._conn.executescript(FTS_SCHEMA)
                self._fts = True
            except sqlite3.DatabaseError:
                self._fts = False
            version = self.get_meta("schema_version")
            if version is None:
                self.set_meta("schema_version", str(SCHEMA_VERSION))
                self.set_meta("created_at", str(now_ts()))
            self._conn.commit()

    def _apply_script(self, script: str) -> list[str]:
        """Выполняет DDL по одному выражению.

        Возвращает те, что не прошли из-за отсутствующих колонок/таблиц: на
        базе, созданной старой версией, ``CREATE INDEX ... ON gifts(on_chain)``
        падает раньше, чем миграция успевает добавить колонку. Такие выражения
        повторяются после ``_migrate_columns`` — иначе движок не стартует.
        """
        pending: list[str] = []
        buffer = ""
        for line in script.splitlines(keepends=True):
            buffer += line
            if not sqlite3.complete_statement(buffer):
                continue
            statement = buffer.strip()
            buffer = ""
            if not statement or _is_comment_only(statement):
                continue
            try:
                self._conn.execute(statement)
            except sqlite3.ProgrammingError:
                # на одной строке оказалось несколько выражений
                self._conn.executescript(statement)
            except sqlite3.OperationalError as exc:
                text = str(exc).lower()
                if "no such column" in text or "no such table" in text:
                    pending.append(statement)
                else:
                    raise
        tail = buffer.strip()
        if tail and not _is_comment_only(tail):
            try:
                self._conn.execute(tail)
            except sqlite3.ProgrammingError:
                self._conn.executescript(tail)
            except sqlite3.OperationalError as exc:
                text = str(exc).lower()
                if "no such column" in text or "no such table" in text:
                    pending.append(tail)
                else:
                    raise
        return pending

    def _soft(self, sql: str) -> None:
        """Миграция, которая не имеет права уронить старт движка."""
        try:
            self._conn.execute(sql)
        except sqlite3.DatabaseError:
            log.warning("шаг миграции пропущен: %s", sql.strip().splitlines()[0][:90])

    def _migrate_columns(self) -> None:
        """Догоняющие ALTER TABLE для баз, созданных старыми версиями."""
        cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(groups)")}
        if "link" not in cols:
            self._soft("ALTER TABLE groups ADD COLUMN link TEXT")
        # ссылки для уже собранных чатов: публичные — по username,
        # приватные супергруппы/каналы — рабочая t.me/c/… для участников
        self._soft(
            "UPDATE groups SET link = 'https://t.me/' || username "
            "WHERE link IS NULL AND username IS NOT NULL AND username <> ''"
        )
        self._soft(
            "UPDATE groups SET link = 'https://t.me/c/' || "
            "(CASE WHEN tg_id < 0 THEN -tg_id - 1000000000000 ELSE tg_id END) || '/1' "
            "WHERE link IS NULL AND kind IN ('channel', 'supergroup') AND tg_id <> 0"
        )
        target_cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(targets)")}
        target_additions = {
            "photo_path": "TEXT",
            "photo_updated_at": "INTEGER",
            "gender": "TEXT",
            "gender_confidence": "REAL",
            "country": "TEXT",
            "country_name": "TEXT",
            "country_confidence": "REAL",
            "intel_source": "TEXT",
            "intel_updated_at": "INTEGER",
            "gifts_count": "INTEGER NOT NULL DEFAULT 0",
            "gifts_updated_at": "INTEGER",
        }
        for column, ddl in target_additions.items():
            if column not in target_cols:
                self._soft(f"ALTER TABLE targets ADD COLUMN {column} {ddl}")
        gift_cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(gifts)")}
        gift_additions = {
            "slug": "TEXT",
            "num": "INTEGER",
            "value_amount": "INTEGER",
            "value_currency": "TEXT",
            "resell_amount": "INTEGER",
            "availability_issued": "INTEGER",
            "availability_total": "INTEGER",
            "on_chain": "INTEGER NOT NULL DEFAULT 0",
            "gift_address": "TEXT",
            "owner_address": "TEXT",
            "value_usd": "INTEGER",
            "burned": "INTEGER NOT NULL DEFAULT 0",
            "crafted": "INTEGER",
            "presence": "TEXT NOT NULL DEFAULT 'visible'",
        }
        for column, ddl in gift_additions.items():
            if column not in gift_cols:
                self._soft(f"ALTER TABLE gifts ADD COLUMN {column} {ddl}")
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

    def backup_to(self, dest: str | Path) -> Path:
        """Консистентная копия базы (sqlite backup API) — можно копировать на ходу."""
        target_path = Path(dest)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._conn.commit()
            target = sqlite3.connect(str(target_path))
            try:
                self._conn.backup(target)
            finally:
                target.close()
        return target_path

    def reopen(self) -> None:
        """Переоткрывает соединение (после восстановления базы из копии)."""
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = sqlite3.connect(
                str(self.path), timeout=10.0, check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._configure()
            self._migrate()

    def repair_session_paths(self, sessions_dir: str | Path) -> int:
        """Чинит абсолютные пути к .session после переустановки/восстановления."""
        directory = Path(sessions_dir)
        fixed = 0
        with self.tx() as conn:
            rows = conn.execute("SELECT id, name, session_file FROM sessions").fetchall()
            for row in rows:
                current = row["session_file"] or ""
                file_name = Path(current).name if current else f"{row['name']}.session"
                target = directory / file_name
                if not target.exists():
                    fallback = directory / f"{row['name']}.session"
                    if fallback.exists():
                        target = fallback
                if str(target) != current:
                    conn.execute(
                        "UPDATE sessions SET session_file = ? WHERE id = ?",
                        (str(target), row["id"]),
                    )
                    fixed += 1
        return fixed

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

    def update_group(
        self,
        tg_id: int,
        *,
        title: str | None = None,
        username: str | None = None,
        link: str | None = None,
        members_count: int | None = None,
        kind: str | None = None,
    ) -> bool:
        """Правит карточку чата, добавленного вручную.

        ``username=""`` очищает публичный адрес (и снимает ``is_public``),
        ``None`` оставляет поле без изменений.
        """
        sets: list[str] = []
        params: list[Any] = []
        if title is not None:
            sets.append("title = ?")
            params.append(title.strip())
        if username is not None:
            clean = username.strip().lstrip("@") or None
            sets.append("username = ?")
            params.append(clean)
            sets.append("is_public = ?")
            params.append(int(bool(clean)))
            if clean and link is None:
                sets.append("link = ?")
                params.append(f"https://t.me/{clean}")
        if link is not None:
            sets.append("link = ?")
            params.append(link.strip() or None)
        if members_count is not None:
            sets.append("members_count = ?")
            params.append(int(members_count))
        if kind is not None:
            sets.append("kind = ?")
            params.append(kind)
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.append(now_ts())
        params.append(tg_id)
        with self.tx() as conn:
            cur = conn.execute(f"UPDATE groups SET {', '.join(sets)} WHERE tg_id = ?", params)
        return cur.rowcount > 0

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

    def update_target_profile(self, tg_user_id: int, **fields: Any) -> None:
        """Обновляет только известные поля профиля цели."""
        allowed = {
            "username",
            "first_name",
            "last_name",
            "bio",
            "photo_path",
            "photo_updated_at",
            "gender",
            "gender_confidence",
            "country",
            "country_name",
            "country_confidence",
            "intel_source",
            "intel_updated_at",
            "gifts_count",
            "gifts_updated_at",
        }
        data = {key: value for key, value in fields.items() if key in allowed}
        if not data:
            return
        data["updated_at"] = now_ts()
        assignments = ", ".join(f"{key} = ?" for key in data)
        with self.tx() as conn:
            conn.execute(
                f"UPDATE targets SET {assignments} WHERE tg_user_id = ?",
                (*data.values(), tg_user_id),
            )

    # ── подарки (star gifts) ────────────────────────────────────────────────

    def add_gift(
        self,
        owner_tg_id: int,
        *,
        saved_id: int | None = None,
        gift_id: int | None = None,
        title: str | None = None,
        kind: str = "regular",
        stars: int | None = None,
        from_tg_id: int | None = None,
        from_username: str | None = None,
        from_name: str | None = None,
        owner_username: str | None = None,
        owner_name: str | None = None,
        message: str | None = None,
        date: int | None = None,
        is_nft: bool = False,
        name_hidden: bool = False,
        slug: str | None = None,
        num: int | None = None,
        value_amount: int | None = None,
        value_currency: str | None = None,
        resell_amount: int | None = None,
        availability_issued: int | None = None,
        availability_total: int | None = None,
        on_chain: bool = False,
        gift_address: str | None = None,
        owner_address: str | None = None,
        value_usd: int | None = None,
        burned: bool = False,
        crafted: int | None = None,
        raw: str | None = None,
    ) -> bool:
        """Сохраняет подарок. True — если он появился в базе впервые."""
        with self.tx() as conn:
            if is_nft and slug:
                existing = conn.execute(
                    "SELECT id FROM gifts WHERE owner_tg_id = ? AND slug = ? ORDER BY id LIMIT 1",
                    (owner_tg_id, slug),
                ).fetchone()
            elif saved_id is None:
                existing = conn.execute(
                    "SELECT id FROM gifts WHERE owner_tg_id = ? AND saved_id IS NULL "
                    "AND gift_id IS ? AND date IS ? AND from_tg_id IS ? ORDER BY id LIMIT 1",
                    (owner_tg_id, gift_id, date, from_tg_id),
                ).fetchone()
            else:
                existing = conn.execute(
                    "SELECT id FROM gifts WHERE owner_tg_id = ? AND saved_id = ? AND gift_id IS ?",
                    (owner_tg_id, saved_id, gift_id),
                ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO gifts(owner_tg_id, saved_id, gift_id, title, kind, stars,
                                      from_tg_id, from_username, from_name, owner_username,
                                      owner_name, message, date, is_nft, name_hidden,
                                      slug, num, value_amount, value_currency, resell_amount,
                                      availability_issued, availability_total, on_chain,
                                      gift_address, owner_address, value_usd, burned, crafted,
                                      raw, seen_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        owner_tg_id,
                        saved_id,
                        gift_id,
                        title,
                        kind,
                        stars,
                        from_tg_id,
                        from_username,
                        from_name,
                        owner_username,
                        owner_name,
                        message,
                        date,
                        int(is_nft),
                        int(name_hidden),
                        slug,
                        num,
                        value_amount,
                        value_currency,
                        resell_amount,
                        availability_issued,
                        availability_total,
                        int(on_chain),
                        gift_address,
                        owner_address,
                        value_usd,
                        int(burned),
                        crafted,
                        raw,
                        now_ts(),
                    ),
                )
                return True
            conn.execute(
                """
                UPDATE gifts SET title = coalesce(?, title), stars = coalesce(?, stars),
                       from_tg_id = coalesce(?, from_tg_id),
                       from_username = coalesce(?, from_username),
                       from_name = coalesce(?, from_name),
                       owner_username = coalesce(?, owner_username),
                       owner_name = coalesce(?, owner_name),
                       message = coalesce(?, message), date = coalesce(?, date),
                       is_nft = ?, name_hidden = ?, raw = coalesce(?, raw),
                       slug = coalesce(?, slug), num = coalesce(?, num),
                       value_amount = coalesce(?, value_amount),
                       value_currency = coalesce(?, value_currency),
                       resell_amount = coalesce(?, resell_amount),
                       availability_issued = coalesce(?, availability_issued),
                       availability_total = coalesce(?, availability_total),
                       on_chain = ?,
                       gift_address = ?,
                       owner_address = ?,
                       value_usd = coalesce(?, value_usd), burned = max(burned, ?),
                       crafted = coalesce(?, crafted),
                       seen_at = ?, presence = 'visible'
                 WHERE id = ?
                """,
                (
                    title,
                    stars,
                    from_tg_id,
                    from_username,
                    from_name,
                    owner_username,
                    owner_name,
                    message,
                    date,
                    int(is_nft),
                    int(name_hidden),
                    raw,
                    slug,
                    num,
                    value_amount,
                    value_currency,
                    resell_amount,
                    availability_issued,
                    availability_total,
                    int(on_chain),
                    gift_address,
                    owner_address,
                    value_usd,
                    int(burned),
                    crafted,
                    now_ts(),
                    existing["id"],
                ),
            )
            return False

    def gift_observation_id(
        self, owner: int, saved_id: int | None, gift_id: int | None,
        *, slug: str | None, date: int | None, sender: int | None,
    ) -> int:
        if slug:
            row = self.query_one(
                "SELECT id FROM gifts WHERE owner_tg_id = ? AND slug = ? ORDER BY id LIMIT 1",
                (owner, slug),
            )
        elif saved_id is None:
            row = self.query_one(
                "SELECT id FROM gifts WHERE owner_tg_id = ? AND saved_id IS NULL "
                "AND gift_id IS ? AND date IS ? AND from_tg_id IS ? ORDER BY id LIMIT 1",
                (owner, gift_id, date, sender),
            )
        else:
            row = self.query_one(
                "SELECT id FROM gifts WHERE owner_tg_id = ? AND saved_id = ? AND gift_id IS ?",
                (owner, saved_id, gift_id),
            )
        if not row:
            raise RuntimeError("Не удалось найти сохранённый подарок")
        return int(row["id"])

    def finish_gift_snapshot(self, owner: int, observed_ids: set[int]) -> None:
        """Отмечаем отсутствующие подарки только после полного успешного чтения."""
        with self.tx() as conn:
            rows = conn.execute("SELECT id, is_nft FROM gifts WHERE owner_tg_id = ? AND presence = 'visible'", (owner,)).fetchall()
            for row in rows:
                if int(row["id"]) not in observed_ids:
                    status = "hidden" if row["is_nft"] else "hidden_or_sold"
                    conn.execute("UPDATE gifts SET presence = ? WHERE id = ?", (status, row["id"]))
            conn.execute("DELETE FROM gift_heavy_profiles WHERE owner_tg_id = ?", (owner,))
            conn.execute(
                "INSERT INTO gift_profile_state(owner_tg_id, completed_at) VALUES (?, ?) "
                "ON CONFLICT(owner_tg_id) DO UPDATE SET completed_at = excluded.completed_at",
                (owner, now_ts()),
            )

    def remember_gift_sticker(self, gift_id: int, document: bytes) -> None:
        if gift_id <= 0 or not document or len(document) > 100_000:
            return
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO gift_stickers(gift_id, document, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(gift_id) DO UPDATE SET document=excluded.document, updated_at=excluded.updated_at "
                "WHERE document != excluded.document", (gift_id, document, now_ts()),
            )

    def gift_sticker(self, gift_id: int) -> bytes | None:
        row = self.query_one("SELECT document FROM gift_stickers WHERE gift_id = ?", (gift_id,))
        return bytes(row["document"]) if row else None

    def create_gift_crawl(self, query: str, depth: int, max_profiles: int, per_profile: int) -> int:
        with self.tx() as conn:
            cur = conn.execute(
                "INSERT INTO gift_crawl_runs(query, depth, max_profiles, per_profile, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)", (query, depth, max_profiles, per_profile, now_ts(), now_ts()),
            )
            return int(cur.lastrowid)

    def gift_crawl(self, run_id: int) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM gift_crawl_runs WHERE id = ?", (run_id,))

    def gift_crawls(self, statuses: tuple[str, ...] = ("running", "paused", "error")) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in statuses)
        return self.query(f"SELECT * FROM gift_crawl_runs WHERE status IN ({marks}) ORDER BY id DESC", statuses)

    def gift_crawl_status(self, run_id: int, status: str, error: str | None = None) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE gift_crawl_runs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                         (status, error, now_ts(), run_id))

    def gift_crawl_enqueue(
        self, run_id: int, tg_id: int, level: int, username: str | None = None,
        access_hash: int | None = None,
    ) -> None:
        if tg_id <= 0:
            return
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO gift_crawl_queue(run_id, tg_id, username, access_hash, level)
                   VALUES (?, ?, ?, ?, ?) ON CONFLICT(run_id, tg_id) DO UPDATE SET
                   username=coalesce(excluded.username, username),
                   access_hash=coalesce(excluded.access_hash, access_hash),
                   level=min(level, excluded.level)""",
                (run_id, tg_id, username, access_hash, level),
            )

    def init_gift_crawl(self, run_id: int, seed_id: int, username: str | None,
                        access_hash: int | None, include_existing: bool) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO gift_crawl_queue(run_id, tg_id, username, access_hash, level) VALUES (?, ?, ?, ?, 0)",
                (run_id, seed_id, username, access_hash),
            )
            if include_existing:
                conn.execute(
                    """INSERT OR IGNORE INTO gift_crawl_queue(run_id, tg_id, username, level)
                       SELECT ?, from_tg_id, max(from_username), 1 FROM gifts
                       WHERE from_tg_id > 0 GROUP BY from_tg_id""", (run_id,),
                )
                conn.execute(
                    """INSERT OR IGNORE INTO gift_crawl_queue(run_id, tg_id, username, level)
                       SELECT ?, owner_tg_id, max(owner_username), 1 FROM gifts
                       WHERE owner_tg_id > 0 GROUP BY owner_tg_id""", (run_id,),
                )
            conn.execute("UPDATE gift_crawl_runs SET initialized = 1, updated_at = ? WHERE id = ?",
                         (now_ts(), run_id))

    def gift_crawl_next(self, run_id: int) -> dict[str, Any] | None:
        return self.query_one(
            "SELECT * FROM gift_crawl_queue WHERE run_id = ? AND state = 'pending' "
            "ORDER BY level, rowid LIMIT 1", (run_id,),
        )

    def gift_crawl_finish_item(self, run_id: int, tg_id: int, state: str, error: str | None = None) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE gift_crawl_queue SET state = ?, error = ? WHERE run_id = ? AND tg_id = ?",
                         (state, error, run_id, tg_id))
            conn.execute("UPDATE gift_crawl_runs SET updated_at = ? WHERE id = ?", (now_ts(), run_id))

    def gift_crawl_counts(self, run_id: int) -> dict[str, int]:
        rows = self.query("SELECT state, COUNT(*) AS n FROM gift_crawl_queue WHERE run_id = ? GROUP BY state", (run_id,))
        counts = {r["state"]: int(r["n"]) for r in rows}
        return {"pending": counts.get("pending", 0), "done": counts.get("done", 0),
                "deferred": counts.get("deferred", 0), "unavailable": counts.get("unavailable", 0),
                "total": sum(counts.values())}

    def gift_profile_known(self, owner: int) -> bool:
        return bool(self.scalar(
            "SELECT EXISTS(SELECT 1 FROM gift_profile_state WHERE owner_tg_id = ?) "
            "OR EXISTS(SELECT 1 FROM gift_heavy_profiles WHERE owner_tg_id = ?)",
            (owner, owner), 0,
        ))

    def gift_profile_senders(self, owner: int) -> list[dict[str, Any]]:
        return self.query(
            "SELECT from_tg_id, max(from_username) AS username FROM gifts "
            "WHERE owner_tg_id = ? AND from_tg_id IS NOT NULL "
            "GROUP BY from_tg_id ORDER BY max(date) DESC", (owner,),
        )

    def observe_nft_owner(self, slug: str, owner: int, username: str | None, name: str | None) -> None:
        """Хронология наблюдений, не утверждение о времени или способе передачи."""
        with self.tx() as conn:
            last = conn.execute(
                "SELECT owner_tg_id FROM nft_owner_history WHERE slug = ? ORDER BY id DESC LIMIT 1", (slug,)
            ).fetchone()
            if last is None:
                previous = conn.execute(
                    "SELECT owner_tg_id, owner_username, owner_name, seen_at FROM nft_items WHERE slug = ?", (slug,)
                ).fetchone()
                if previous and previous["owner_tg_id"] and int(previous["owner_tg_id"]) != owner:
                    conn.execute(
                        "INSERT INTO nft_owner_history(slug, owner_tg_id, owner_username, owner_name, observed_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (slug, previous["owner_tg_id"], previous["owner_username"], previous["owner_name"], previous["seen_at"]),
                    )
            if last is None or int(last["owner_tg_id"]) != owner:
                # Исторические строки других владельцев сохраняются.
                conn.execute(
                    "UPDATE gifts SET presence = 'previous_owner' "
                    "WHERE slug = ? AND owner_tg_id != ? AND is_nft = 1", (slug, owner),
                )
                conn.execute(
                    "INSERT INTO nft_owner_history(slug, owner_tg_id, owner_username, owner_name, observed_at) "
                    "VALUES (?, ?, ?, ?, ?)", (slug, owner, username, name, now_ts()),
                )

    def nft_history(self, slug: str) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM nft_owner_history WHERE slug = ? ORDER BY id", (slug,))

    def defer_gift_profile(self, owner: int, username: str | None, name: str | None, count: int) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO gift_heavy_profiles(owner_tg_id, username, name, gift_count, detected_at)
                   VALUES (?, ?, ?, ?, ?) ON CONFLICT(owner_tg_id) DO UPDATE SET
                   username=excluded.username, name=excluded.name,
                   gift_count=excluded.gift_count, detected_at=excluded.detected_at""",
                (owner, username, name, count, now_ts()),
            )

    def heavy_gift_profiles(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM gift_heavy_profiles ORDER BY detected_at DESC LIMIT ?", (limit,))

    def list_gifts(
        self,
        owner_tg_id: int | None = None,
        *,
        nft_only: bool = False,
        chain: str | None = None,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """``chain``: ``wallet`` — NFT на TON-кошельке, ``telegram`` — внутри TG."""
        where: list[str] = []
        params: list[Any] = []
        if owner_tg_id is not None:
            where.append("owner_tg_id = ?")
            params.append(owner_tg_id)
        if nft_only:
            where.append("is_nft = 1")
        if chain == "wallet":
            where.append("on_chain = 1")
        elif chain == "telegram":
            where.append("on_chain = 0")
        if q:
            needle = f"%{q.strip()}%"
            where.append(
                "(owner_username LIKE ? OR owner_name LIKE ? OR from_username LIKE ? "
                "OR title LIKE ? OR slug LIKE ?)"
            )
            params.extend([needle] * 5)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.extend([max(1, min(1000, limit)), max(0, offset)])
        return self.query(
            f"SELECT * FROM gifts {clause} ORDER BY coalesce(date, 0) DESC, id DESC LIMIT ? OFFSET ?",
            params,
        )

    def count_gifts(
        self, owner_tg_id: int | None = None, *, nft_only: bool = False,
        chain: str | None = None, q: str | None = None,
    ) -> int:
        where: list[str] = []
        params: list[Any] = []
        if owner_tg_id is not None:
            where.append("owner_tg_id = ?")
            params.append(owner_tg_id)
        if nft_only:
            where.append("is_nft = 1")
        if chain == "wallet":
            where.append("on_chain = 1")
        elif chain == "telegram":
            where.append("on_chain = 0")
        if q:
            where.append("(owner_username LIKE ? OR owner_name LIKE ? OR from_username LIKE ? "
                         "OR title LIKE ? OR slug LIKE ?)")
            params.extend([f"%{q.strip()}%"] * 5)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        return int(self.scalar(f"SELECT COUNT(*) FROM gifts {clause}", params, 0))

    def gift_summary(self) -> dict[str, Any]:
        row = self.query_one(
            """
            SELECT COUNT(*) AS total,
                   coalesce(SUM(is_nft), 0) AS nft,
                   coalesce(SUM(CASE WHEN is_nft = 1 AND on_chain = 1 THEN 1 ELSE 0 END), 0)
                       AS nft_chain,
                   coalesce(SUM(CASE WHEN is_nft = 1 AND on_chain = 0 THEN 1 ELSE 0 END), 0)
                       AS nft_hosted,
                   COUNT(DISTINCT owner_tg_id) AS owners,
                   COUNT(DISTINCT from_tg_id) AS senders
              FROM gifts
            """
        ) or {}
        inventory = self.nft_summary()
        return {
            "total": int(row.get("total") or 0),
            "nft": int(row.get("nft") or 0),
            "nft_chain": int(row.get("nft_chain") or 0),
            "nft_hosted": int(row.get("nft_hosted") or 0),
            "owners": int(row.get("owners") or 0),
            "senders": int(row.get("senders") or 0),
            "inventory": inventory["total"],
            "inventory_chain": inventory["on_chain"],
            "inventory_hosted": inventory["hosted"],
            "inventory_owners": inventory["owners"],
        }

    # ── NFT-инвентарь (уникальные подарки) ──────────────────────────────────

    def upsert_nft_item(self, slug: str, **fields: Any) -> bool:
        """Пишет уникальную вещь в инвентарь. True — если она новая."""
        slug = (slug or "").strip()
        if not slug:
            return False
        columns = {
            "gift_id": fields.get("gift_id"),
            "num": fields.get("num"),
            "title": fields.get("title"),
            "owner_tg_id": fields.get("owner_tg_id"),
            "owner_username": fields.get("owner_username"),
            "owner_name": fields.get("owner_name"),
            "owner_address": fields.get("owner_address"),
            "gift_address": fields.get("gift_address"),
            "on_chain": int(bool(fields.get("on_chain"))),
            "burned": int(bool(fields.get("burned"))),
            "crafted": fields.get("crafted"),
            "value_amount": fields.get("value_amount"),
            "value_currency": fields.get("value_currency"),
            "value_usd": fields.get("value_usd"),
            "resell_amount": fields.get("resell_amount"),
            "availability_issued": fields.get("availability_issued"),
            "availability_total": fields.get("availability_total"),
        }
        with self.tx() as conn:
            exists = conn.execute(
                "SELECT 1 FROM nft_items WHERE slug = ?", (slug,)
            ).fetchone()
            if exists is None:
                names = ", ".join(("slug", *columns.keys(), "seen_at"))
                marks = ", ".join("?" for _ in range(len(columns) + 2))
                conn.execute(
                    f"INSERT INTO nft_items({names}) VALUES({marks})",
                    (slug, *columns.values(), now_ts()),
                )
                return True
            authoritative = bool(fields.get("ownership_observed"))
            assignments = ", ".join(
                (f"{name} = max({name}, ?)" if name == "burned" or (name == "on_chain" and not authoritative)
                 else f"{name} = ?" if authoritative and name in (
                     "on_chain", "gift_address", "owner_address", "owner_tg_id", "owner_username", "owner_name"
                 ) else f"{name} = coalesce(?, {name})")
                for name in columns
            )
            conn.execute(
                f"UPDATE nft_items SET {assignments}, seen_at = ? WHERE slug = ?",
                (*columns.values(), now_ts(), slug),
            )
            return False

    def _nft_where(
        self,
        *,
        chain: str | None = None,
        q: str | None = None,
        resale_only: bool = False,
        collection: str | None = None,
    ) -> tuple[str, list[Any]]:
        """Общее условие выборки NFT — одно и то же для списка, счётчика и CSV."""
        where: list[str] = []
        params: list[Any] = []
        if chain == "wallet":
            where.append("on_chain = 1")
        elif chain == "telegram":
            where.append("on_chain = 0")
        if collection:
            where.append("slug LIKE ?")
            params.append(f"{collection.strip()}-%")
        if resale_only:
            where.append("coalesce(resell_amount, 0) > 0")
        if q:
            needle = f"%{q.strip()}%"
            where.append(
                "(slug LIKE ? OR title LIKE ? OR owner_username LIKE ? OR owner_name LIKE ? "
                "OR owner_address LIKE ? OR gift_address LIKE ? "
                "OR CAST(owner_tg_id AS TEXT) LIKE ? OR CAST(num AS TEXT) = ?)"
            )
            params.extend([needle, needle, needle, needle, needle, needle, needle, q.strip()])
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        return clause, params

    def nft_items(
        self,
        *,
        chain: str | None = None,
        q: str | None = None,
        sort: str = "value",
        limit: int = 100,
        offset: int = 0,
        resale_only: bool = False,
        collection: str | None = None,
    ) -> list[dict[str, Any]]:
        clause, params = self._nft_where(
            chain=chain, q=q, resale_only=resale_only, collection=collection
        )
        order = {
            "value": "coalesce(value_usd, value_amount, 0) DESC, seen_at DESC",
            "resell": "coalesce(resell_amount, 0) DESC, seen_at DESC",
            "recent": "seen_at DESC",
            "num": "num ASC",
        }.get(sort, "seen_at DESC")
        params.extend([max(1, min(1000, limit)), max(0, offset)])
        return self.query(
            f"SELECT nft_items.*, (SELECT presence FROM gifts WHERE gifts.slug = nft_items.slug "
            f"AND gifts.owner_tg_id = nft_items.owner_tg_id ORDER BY id LIMIT 1) AS owner_presence "
            f"FROM nft_items {clause} ORDER BY {order} LIMIT ? OFFSET ?", params
        )

    def nft_count(
        self,
        *,
        chain: str | None = None,
        q: str | None = None,
        resale_only: bool = False,
        collection: str | None = None,
    ) -> int:
        """Сколько всего NFT подходит под фильтр — для «найдено N из M»."""
        clause, params = self._nft_where(
            chain=chain, q=q, resale_only=resale_only, collection=collection
        )
        row = self.query_one(f"SELECT COUNT(*) AS n FROM nft_items {clause}", params) or {}
        return int(row.get("n") or 0)

    def nft_summary(self) -> dict[str, Any]:
        row = self.query_one(
            """
            SELECT COUNT(*) AS total,
                   coalesce(SUM(on_chain), 0) AS on_chain,
                   COUNT(DISTINCT CASE WHEN owner_tg_id IS NOT NULL THEN owner_tg_id END)
                       AS owners,
                   coalesce(SUM(burned), 0) AS burned
              FROM nft_items
            """
        ) or {}
        total = int(row.get("total") or 0)
        on_chain = int(row.get("on_chain") or 0)
        collections = self.query_one(
            """
            SELECT COUNT(*) AS collections,
                   coalesce(SUM(total), 0) AS capacity,
                   coalesce(SUM(scanned), 0) AS scanned
              FROM nft_collections
            """
        ) or {}
        return {
            "total": total,
            "on_chain": on_chain,
            "hosted": max(0, total - on_chain),
            "owners": int(row.get("owners") or 0),
            "burned": int(row.get("burned") or 0),
            "collections": int(collections.get("collections") or 0),
            "capacity": int(collections.get("capacity") or 0),
            "scanned": int(collections.get("scanned") or 0),
        }

    def nft_owners(
        self,
        *,
        chain: str | None = None,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Владельцы NFT по инвентарю + по собранным подаркам."""
        where = ["owner_tg_id IS NOT NULL"]
        params: list[Any] = []
        if chain == "wallet":
            where.append("on_chain = 1")
        elif chain == "telegram":
            where.append("on_chain = 0")
        if q:
            needle = f"%{q.strip()}%"
            where.append(
                "(owner_username LIKE ? OR owner_name LIKE ? OR CAST(owner_tg_id AS TEXT) LIKE ?)"
            )
            params.extend([needle, needle, needle])
        params.extend([max(1, min(1000, limit)), max(0, offset)])
        return self.query(
            f"""
            SELECT owner_tg_id, max(owner_username) AS owner_username,
                   max(owner_name) AS owner_name, COUNT(*) AS nft,
                   coalesce(SUM(on_chain), 0) AS on_chain,
                   coalesce(SUM(CASE WHEN on_chain = 0 THEN 1 ELSE 0 END), 0) AS hosted,
                   max(seen_at) AS seen_at
              FROM nft_items
             WHERE {' AND '.join(where)}
             GROUP BY owner_tg_id
             ORDER BY nft DESC, seen_at DESC LIMIT ? OFFSET ?
            """,
            params,
        )

    def all_gift_owners(self, q: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Все известные владельцы, включая обычные подарки и отложенные профили."""
        needle = f"%{(q or '').strip()}%"
        return self.query(
            """WITH ids AS (
                   SELECT owner_tg_id AS id FROM gifts WHERE owner_tg_id IS NOT NULL
                   UNION SELECT owner_tg_id FROM nft_items WHERE owner_tg_id IS NOT NULL
                   UNION SELECT owner_tg_id FROM gift_heavy_profiles
               ), g AS (
                   SELECT owner_tg_id, max(owner_username) AS username, max(owner_name) AS name,
                          COUNT(*) AS gifts, max(seen_at) AS seen_at
                     FROM gifts GROUP BY owner_tg_id
               ), n AS (
                   SELECT owner_tg_id, max(owner_username) AS username, max(owner_name) AS name,
                          COUNT(*) AS nft, sum(on_chain) AS on_chain,
                          sum(CASE WHEN on_chain = 0 THEN 1 ELSE 0 END) AS hosted, max(seen_at) AS seen_at
                     FROM nft_items WHERE owner_tg_id IS NOT NULL GROUP BY owner_tg_id
               )
               SELECT ids.id AS owner_tg_id, coalesce(g.username, n.username, h.username) AS owner_username,
                      coalesce(g.name, n.name, h.name) AS owner_name, coalesce(g.gifts, 0) AS gifts,
                      coalesce(n.nft, 0) AS nft, coalesce(n.on_chain, 0) AS on_chain,
                      coalesce(n.hosted, 0) AS hosted,
                      max(coalesce(g.seen_at, 0), coalesce(n.seen_at, 0), coalesce(h.detected_at, 0)) AS seen_at,
                      h.gift_count AS deferred_count
                 FROM ids LEFT JOIN g ON g.owner_tg_id = ids.id
                          LEFT JOIN n ON n.owner_tg_id = ids.id
                          LEFT JOIN gift_heavy_profiles h ON h.owner_tg_id = ids.id
                WHERE coalesce(g.username, n.username, h.username, '') LIKE ?
                   OR coalesce(g.name, n.name, h.name, '') LIKE ? OR CAST(ids.id AS TEXT) LIKE ?
                ORDER BY gifts DESC, nft DESC, seen_at DESC LIMIT ? OFFSET ?""",
            (needle, needle, needle, max(1, min(1000, int(limit))), max(0, int(offset))),
        )

    def nft_owners_fresh(self, limit: int = 200) -> list[dict[str, Any]]:
        """Владельцы NFT, чьи подарки ещё не читали (для следующего шага обхода)."""
        return self.query(
            """
            SELECT i.owner_tg_id, max(i.owner_username) AS owner_username,
                   max(i.owner_name) AS owner_name, COUNT(*) AS nft
              FROM nft_items i
              LEFT JOIN (SELECT DISTINCT owner_tg_id AS gid FROM gifts) g
                     ON g.gid = i.owner_tg_id
             WHERE i.owner_tg_id IS NOT NULL AND g.gid IS NULL
             GROUP BY i.owner_tg_id
             ORDER BY nft DESC LIMIT ?
            """,
            (max(1, min(20000, limit)),),
        )

    def nft_collections(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT * FROM nft_collections
             ORDER BY (scanned < total) DESC, total DESC LIMIT ?
            """,
            (max(1, min(500, limit)),),
        )

    def upsert_nft_collection(
        self,
        slug_base: str,
        *,
        gift_id: int | None = None,
        title: str | None = None,
        total: int | None = None,
    ) -> None:
        slug_base = (slug_base or "").strip()
        if not slug_base:
            return
        with self.tx() as conn:
            conn.execute(
                """
                INSERT INTO nft_collections(slug_base, gift_id, title, total, cursor,
                                            scanned, updated_at)
                VALUES(?,?,?,?,1,0,?)
                ON CONFLICT(slug_base) DO UPDATE SET
                    gift_id = coalesce(excluded.gift_id, gift_id),
                    title = coalesce(excluded.title, title),
                    total = max(total, coalesce(excluded.total, 0))
                """,
                (slug_base, gift_id, title, max(0, int(total or 0)), now_ts()),
            )

    def next_nft_collection(self) -> dict[str, Any] | None:
        row = self.query_one(
            """
            SELECT * FROM nft_collections
             WHERE total > 0 AND cursor <= total
             ORDER BY (scanned < total) DESC, updated_at ASC LIMIT 1
            """
        )
        return row

    def mark_nft_collection_done(self, slug_base: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE nft_collections SET cursor = max(cursor, total + 1), updated_at = ? "
                "WHERE slug_base = ?",
                (now_ts(), slug_base),
            )

    def advance_nft_collection(
        self,
        slug_base: str,
        *,
        cursor: int,
        scanned: int,
        owners: int = 0,
        on_chain: int = 0,
        error: str | None = None,
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                """
                UPDATE nft_collections SET cursor = ?, scanned = ?,
                       owners = owners + ?, on_chain = on_chain + ?,
                       errors = errors + ?, last_error = coalesce(?, last_error),
                       updated_at = ?
                 WHERE slug_base = ?
                """,
                (
                    int(cursor),
                    int(scanned),
                    int(owners),
                    int(on_chain),
                    1 if error else 0,
                    error,
                    now_ts(),
                    slug_base,
                ),
            )

    def gift_owners(self, limit: int = 30) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT owner_tg_id, COUNT(*) AS gifts, coalesce(SUM(is_nft), 0) AS nft,
                   max(date) AS last_date, max(owner_username) AS owner_username,
                   max(owner_name) AS owner_name, max(seen_at) AS checked_at
              FROM gifts GROUP BY owner_tg_id
             ORDER BY nft DESC, gifts DESC LIMIT ?
            """,
            (max(1, min(10000, limit)),),
        )

    def gift_graph(self, seed: int | None = None, limit: int = 3000) -> dict[str, Any]:
        """Паутина подарков: узлы — владельцы и дарители, рёбра — подарки."""
        rows = self.query(
            """
            SELECT from_tg_id, owner_tg_id, COUNT(*) AS gifts,
                   coalesce(SUM(stars), 0) AS stars, coalesce(SUM(is_nft), 0) AS nft,
                   max(date) AS last_date
              FROM gifts
             WHERE from_tg_id IS NOT NULL AND from_tg_id <> owner_tg_id
             GROUP BY from_tg_id, owner_tg_id
             ORDER BY gifts DESC, stars DESC LIMIT ?
            """,
            (max(1, min(10000, limit)),),
        )
        if seed is not None:
            rows = [r for r in rows if seed in (r["from_tg_id"], r["owner_tg_id"])]
        nodes: dict[int, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        for row in rows:
            for tg_id in (row["from_tg_id"], row["owner_tg_id"]):
                node = nodes.setdefault(
                    int(tg_id),
                    {"id": f"u{tg_id}", "tg_id": int(tg_id), "weight": 0, "nft": 0},
                )
                node["weight"] += int(row["gifts"] or 0)
                node["nft"] += int(row["nft"] or 0)
            edges.append(
                {
                    "source": f"u{row['from_tg_id']}",
                    "target": f"u{row['owner_tg_id']}",
                    "weight": int(row["gifts"] or 1),
                    "stars": int(row["stars"] or 0),
                    "nft": int(row["nft"] or 0),
                    "last_date": int(row["last_date"] or 0),
                }
            )
        return {"nodes": list(nodes.values()), "edges": edges}

    def touch_gifts(self, tg_user_id: int) -> int:
        total = int(
            self.scalar("SELECT COUNT(*) FROM gifts WHERE owner_tg_id = ?", (tg_user_id,), 0)
        )
        self.update_target_profile(
            tg_user_id, gifts_count=total, gifts_updated_at=now_ts()
        )
        return total

    def delete_target(self, tg_user_id: int) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM targets WHERE tg_user_id = ?", (tg_user_id,))

    def touch_target_scan(self, tg_user_id: int) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE targets SET last_scan_at = ? WHERE tg_user_id = ?", (now_ts(), tg_user_id))

    # ── алерты ─────────────────────────────────────────────────────────────
    def add_alert(
        self,
        kind: str,
        title: str,
        *,
        body: str | None = None,
        target_pk: int | None = None,
        dedup: str | None = None,
        ts: int | None = None,
    ) -> int | None:
        """Создаёт алерт; возвращает id или None, если такой уже был."""
        with self.tx() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO alerts(ts, target_id, kind, title, body, dedup, read)
                VALUES(?,?,?,?,?,?,0)
                """,
                (ts or now_ts(), target_pk, kind, title, body, dedup),
            )
            if not cur.rowcount:
                return None
            return int(cur.lastrowid or 0)

    def list_alerts(self, *, limit: int = 50, unread_only: bool = False) -> list[dict[str, Any]]:
        sql = """
            SELECT a.*, t.tg_user_id, t.username
              FROM alerts a LEFT JOIN targets t ON t.id = a.target_id
        """
        if unread_only:
            sql += " WHERE a.read = 0"
        sql += " ORDER BY a.ts DESC, a.id DESC LIMIT ?"
        return self.query(sql, (int(limit),))

    def count_alerts(self, *, unread_only: bool = True) -> int:
        if unread_only:
            return int(self.scalar("SELECT COUNT(*) FROM alerts WHERE read = 0", default=0))
        return int(self.scalar("SELECT COUNT(*) FROM alerts", default=0))

    def mark_alerts_read(self, ids: Iterable[int] | None = None) -> int:
        with self.tx() as conn:
            if ids is None:
                cur = conn.execute("UPDATE alerts SET read = 1 WHERE read = 0")
            else:
                chunk = [int(i) for i in ids]
                if not chunk:
                    return 0
                placeholders = ",".join("?" for _ in chunk)
                cur = conn.execute(
                    f"UPDATE alerts SET read = 1 WHERE id IN ({placeholders})", chunk
                )
            return int(cur.rowcount or 0)

    def pending_alerts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT a.*, t.tg_user_id, t.username
              FROM alerts a LEFT JOIN targets t ON t.id = a.target_id
             WHERE a.sent_at IS NULL
             ORDER BY a.ts, a.id LIMIT ?
            """,
            (int(limit),),
        )

    def mark_alert_sent(self, alert_id: int) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE alerts SET sent_at = ? WHERE id = ?", (now_ts(), int(alert_id)))

    # ── активность цели: сообщения, собеседники, связи ─────────────────────
    def upsert_peer(
        self,
        tg_id: int,
        *,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> int:
        ts = now_ts()
        with self.tx() as conn:
            conn.execute(
                """
                INSERT INTO peers(tg_id, username, first_name, last_name, updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(tg_id) DO UPDATE SET
                    username   = coalesce(excluded.username, peers.username),
                    first_name = coalesce(excluded.first_name, peers.first_name),
                    last_name  = coalesce(excluded.last_name, peers.last_name),
                    updated_at = excluded.updated_at
                """,
                (int(tg_id), username, first_name, last_name, ts),
            )
            row = conn.execute("SELECT id FROM peers WHERE tg_id = ?", (int(tg_id),)).fetchone()
        return int(row["id"])

    def add_message(
        self,
        target_pk: int,
        group_pk: int,
        tg_msg_id: int,
        *,
        date: int,
        text: str = "",
        reply_to_msg: int | None = None,
        reply_to_peer: int | None = None,
        mentions: str | None = None,
        media: str | None = None,
        views: int | None = None,
    ) -> bool:
        with self.tx() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO messages(
                    target_id, group_id, tg_msg_id, date, text, reply_to_msg,
                    reply_to_peer, mentions, media, views, collected_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    target_pk,
                    group_pk,
                    int(tg_msg_id),
                    int(date),
                    (text or "")[:4000],
                    reply_to_msg,
                    reply_to_peer,
                    mentions,
                    media,
                    views,
                    now_ts(),
                ),
            )
            return cur.rowcount > 0

    def bump_edge(
        self,
        target_pk: int,
        peer_pk: int,
        *,
        replies: int = 0,
        mentions: int = 0,
        ts: int | None = None,
    ) -> None:
        add = int(replies) + int(mentions)
        with self.tx() as conn:
            conn.execute(
                """
                INSERT INTO edges(target_id, peer_id, weight, replies, mentions, last_seen)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(target_id, peer_id) DO UPDATE SET
                    weight    = edges.weight + excluded.weight,
                    replies   = edges.replies + excluded.replies,
                    mentions  = edges.mentions + excluded.mentions,
                    last_seen = max(coalesce(edges.last_seen, 0), coalesce(excluded.last_seen, 0))
                """,
                (target_pk, peer_pk, add, replies, mentions, ts or now_ts()),
            )

    def activity_stats(self, target_pk: int) -> dict[str, Any]:
        row = self.query_one(
            """
            SELECT COUNT(*) AS messages,
                   COUNT(DISTINCT group_id) AS chats,
                   SUM(CASE WHEN reply_to_peer IS NOT NULL THEN 1 ELSE 0 END) AS replies,
                   SUM(CASE WHEN coalesce(mentions, '') <> '' THEN 1 ELSE 0 END) AS mentioned,
                   MIN(date) AS first_date,
                   MAX(date) AS last_date
              FROM messages WHERE target_id = ?
            """,
            (target_pk,),
        ) or {}
        links = self.query_one(
            "SELECT COUNT(*) AS peers, coalesce(SUM(weight), 0) AS weight "
            "FROM edges WHERE target_id = ?",
            (target_pk,),
        ) or {}
        return {
            "messages": int(row.get("messages") or 0),
            "chats": int(row.get("chats") or 0),
            "replies": int(row.get("replies") or 0),
            "mentioned": int(row.get("mentioned") or 0),
            "peers": int(links.get("peers") or 0),
            "weight": int(links.get("weight") or 0),
            "first_date": row.get("first_date"),
            "last_date": row.get("last_date"),
        }

    def activity_feed(self, target_pk: int, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT m.*, g.title, g.username, g.link, g.kind, g.tg_id AS group_tg_id
              FROM messages m JOIN groups g ON g.id = m.group_id
             WHERE m.target_id = ?
             ORDER BY m.date DESC, m.tg_msg_id DESC
             LIMIT ?
            """,
            (target_pk, int(limit)),
        )

    def target_edges(self, target_pk: int, *, limit: int = 200) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT e.*, p.tg_id, p.username, p.first_name, p.last_name
              FROM edges e JOIN peers p ON p.id = e.peer_id
             WHERE e.target_id = ?
             ORDER BY e.weight DESC, coalesce(e.last_seen, 0) DESC
             LIMIT ?
            """,
            (target_pk, int(limit)),
        )

    def chat_activity(self, target_pk: int) -> dict[int, int]:
        rows = self.query(
            "SELECT group_id, COUNT(*) AS n FROM messages WHERE target_id = ? GROUP BY group_id",
            (target_pk,),
        )
        return {int(r["group_id"]): int(r["n"]) for r in rows}

    def prune_activity(self, target_pk: int | None = None, *, days: int = 90) -> int:
        cutoff = now_ts() - max(1, int(days)) * 86400
        sql = "DELETE FROM messages WHERE date < ?"
        params: list[Any] = [cutoff]
        if target_pk is not None:
            sql += " AND target_id = ?"
            params.append(target_pk)
        with self.tx() as conn:
            cur = conn.execute(sql, params)
            return int(cur.rowcount or 0)

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
