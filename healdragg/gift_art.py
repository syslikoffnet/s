"""Оригинальные стикеры обычных подарков из Telegram, с локальным кэшем."""
from __future__ import annotations

import asyncio
import gzip
import io
import json
import logging
import time
import zlib
from pathlib import Path
from typing import Any

from telethon.extensions import BinaryReader
from telethon.tl.functions.payments import GetStarGiftsRequest
from telethon.tl.types import Document

from .db import Store
from .tg import RateLimiter, SessionPool

log = logging.getLogger("healdragg.gift_art")
MAX_ART_SIZE = 2_000_000


def _document(data: bytes | None) -> Document | None:
    if not data:
        return None
    try:
        with BinaryReader(data) as reader:
            doc = reader.tgread_object()
        return doc if isinstance(doc, Document) else None
    except Exception:  # noqa: BLE001
        log.warning("Не удалось прочитать сохранённый стикер")
        return None


def _format(data: bytes) -> str | None:
    if data.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                decoded = stream.read(8_000_001)
            if len(decoded) > 8_000_000:
                return None
            content = json.loads(decoded)
            return "tgs" if isinstance(content, dict) and isinstance(content.get("layers"), list) else None
        except (OSError, EOFError, ValueError, RecursionError, zlib.error):
            return None
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    return None


class GiftArt:
    def __init__(self, home: Path, store: Store, pool: SessionPool) -> None:
        self.directory = home / "gift-art"
        self.store = store
        self.pool = pool
        self._catalog_lock = asyncio.Lock()
        self._catalog_at = float("-inf")
        self._catalog_retry_at = float("-inf")
        self._downloads = asyncio.Semaphore(3)
        self._locks: dict[int, asyncio.Lock] = {}

    def _cached(self, gift_id: int) -> tuple[bytes, str] | None:
        for ext in ("tgs", "webp", "png", "jpg"):
            path = self.directory / f"{gift_id}.{ext}"
            try:
                if path.is_file() and 0 < path.stat().st_size <= MAX_ART_SIZE:
                    data = path.read_bytes()
                    if _format(data) == ext:
                        return data, ext
            except OSError:
                log.warning("Не удалось прочитать кэш иллюстрации %s", gift_id)
        return None

    async def _catalog(self, account: Any, *, force: bool = False) -> None:
        async with self._catalog_lock:
            now = time.monotonic()
            if now < self._catalog_retry_at or now - self._catalog_at < (30 if force else 300):
                return
            limiter = account.limiter or RateLimiter()
            try:
                response = await limiter.call(lambda: account.client(GetStarGiftsRequest(hash=0)))
            except Exception:
                self._catalog_retry_at = time.monotonic() + 30
                raise
            for gift in getattr(response, "gifts", None) or []:
                doc = getattr(gift, "sticker", None)
                gift_id = getattr(gift, "id", None)
                if isinstance(doc, Document) and isinstance(gift_id, int):
                    self.store.remember_gift_sticker(gift_id, bytes(doc))
            self._catalog_at = time.monotonic()

    async def _download(self, account: Any, doc: Document) -> bytes | None:
        limiter = account.limiter or RateLimiter()
        # Анимированные подарки Telegram — TGS; обычные стикеры — WebP.
        if doc.size and 0 < doc.size <= MAX_ART_SIZE and doc.mime_type in (
            "application/x-tgsticker", "image/webp", "image/png", "image/jpeg"
        ):
            data = await limiter.call(lambda: account.client.download_media(doc, file=bytes))
            if isinstance(data, bytes) and len(data) <= MAX_ART_SIZE and _format(data):
                return data
        # Для видео и неподдерживаемых форматов используем настоящий статичный thumbnail.
        if doc.thumbs:
            data = await limiter.call(lambda: account.client.download_media(doc, file=bytes, thumb=-1))
            if isinstance(data, bytes) and len(data) <= MAX_ART_SIZE and _format(data) in ("webp", "png", "jpg"):
                return data
        return None

    async def get(self, gift_id: int) -> tuple[bytes, str] | None:
        if gift_id <= 0:
            return None
        cached = self._cached(gift_id)
        if cached:
            return cached
        lock = self._locks.setdefault(gift_id, asyncio.Lock())
        async with lock:
            cached = self._cached(gift_id)
            if cached:
                return cached
            async with self._downloads:
                try:
                    account = await self.pool.first()
                    doc = _document(self.store.gift_sticker(gift_id))
                    if doc is None:
                        await self._catalog(account)
                        doc = _document(self.store.gift_sticker(gift_id))
                    if doc is None:
                        return None
                    try:
                        data = await self._download(account, doc)
                    except Exception:  # noqa: BLE001 — file_reference может устареть
                        await self._catalog(account, force=True)
                        refreshed = _document(self.store.gift_sticker(gift_id))
                        data = await self._download(account, refreshed) if refreshed else None
                    ext = _format(data) if data else None
                    if not ext or not data:
                        return None
                    self.directory.mkdir(parents=True, exist_ok=True)
                    path = self.directory / f"{gift_id}.{ext}"
                    temporary = path.with_suffix(path.suffix + ".tmp")
                    temporary.write_bytes(data)
                    temporary.replace(path)
                    return data, ext
                except Exception as exc:  # noqa: BLE001 — недоступная картинка не ломает список
                    log.info("Иллюстрация подарка %s недоступна: %s", gift_id, exc)
                    return None
