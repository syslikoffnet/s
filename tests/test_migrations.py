"""Миграции старых баз и работа без tzdata — то, на чём падал движок на телефоне."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import pytest

from healdragg import util
from healdragg.db import Store


def _old_database(path: Path) -> None:
    """База старой версии: в gifts ещё нет NFT-колонок."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE gifts (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id INTEGER,
            gift_id   INTEGER,
            date      INTEGER,
            is_nft    INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO gifts (target_id, gift_id, date, is_nft) VALUES (1, 42, 1700000000, 0);
        """
    )
    conn.commit()
    conn.close()


def test_store_starts_on_old_database(tmp_path: Path) -> None:
    """CREATE INDEX по отсутствующей колонке не должен убивать старт движка."""
    db_path = tmp_path / "old.db"
    _old_database(db_path)

    store = Store(db_path)
    try:
        columns = {row["name"] for row in store.query("PRAGMA table_info(gifts)")}
        assert "on_chain" in columns
        assert "gift_address" in columns
        indexes = {row["name"] for row in store.query("PRAGMA index_list(gifts)")}
        assert "idx_gifts_chain" in indexes
        assert store.query_one("SELECT COUNT(*) AS n FROM gifts")["n"] == 1
    finally:
        store.close()


def test_store_reopens_same_database(tmp_path: Path) -> None:
    db_path = tmp_path / "twice.db"
    _old_database(db_path)
    Store(db_path).close()
    store = Store(db_path)
    try:
        assert store.query_one("SELECT COUNT(*) AS n FROM gifts")["n"] == 1
    finally:
        store.close()


def test_tzinfo_works_without_zoneinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    """На Android нет tzdata: ZoneInfo кидает, а время форматироваться обязано."""

    class BrokenZone:
        def __init__(self, key: str) -> None:
            raise ZoneInfoNotFoundError(key)

    monkeypatch.setattr(util, "ZoneInfo", BrokenZone)
    moscow = util.tzinfo("Europe/Moscow")
    assert moscow.utcoffset(None).total_seconds() == 3 * 3600
    # 1700000000 UTC = 14 нояб. 22:13, в Москве уже 15-е и 01:13.
    assert util.fmt_dt(1700000000) == "15 нояб. 2023, 01:13"
    assert util.tzinfo("UTC").utcoffset(None).total_seconds() == 0
    # Неизвестная зона не должна ронять приложение.
    assert util.tzinfo("Mars/Olympus").utcoffset(None).total_seconds() == 0


def test_tzinfo_handles_any_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(key: str) -> None:
        raise RuntimeError("нет данных о зонах")

    monkeypatch.setattr(util, "ZoneInfo", boom)
    assert util.fmt_day(1700000000) == "15 нояб."
