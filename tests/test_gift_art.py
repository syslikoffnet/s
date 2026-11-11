from __future__ import annotations

import asyncio
import gzip
from datetime import datetime, timezone
from types import SimpleNamespace

from starlette.testclient import TestClient
from telethon.tl import types

from healdragg.api import create_app
from healdragg.config import Settings
from healdragg.db import Store
from healdragg.gift_art import GiftArt
from healdragg.tg import RateLimiter


def sticker(document_id: int = 42) -> types.Document:
    return types.Document(id=document_id, access_hash=123, file_reference=b"ref", date=datetime.now(timezone.utc),
                          mime_type="application/x-tgsticker", size=100, dc_id=2, attributes=[])


class FakeClient:
    def __init__(self):
        self.catalog_calls = 0
        self.download_calls = 0
        self.doc = sticker()
        self.image = gzip.compress(b'{"v":"5.0","layers":[]}')

    async def __call__(self, request):
        self.catalog_calls += 1
        return SimpleNamespace(gifts=[SimpleNamespace(id=1234, sticker=self.doc)])

    async def download_media(self, doc, file, **kwargs):
        self.download_calls += 1
        assert doc.id == self.doc.id and file is bytes
        await asyncio.sleep(0)
        return self.image


class FakePool:
    def __init__(self, client):
        self.client = client
        self.limiter = RateLimiter(0, 0)

    async def first(self):
        return SimpleNamespace(client=self.client, limiter=self.limiter)


async def test_catalog_download_and_offline_cache(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    client = FakeClient()
    art = GiftArt(tmp_path, store, FakePool(client))
    first, second = await asyncio.gather(art.get(1234), art.get(1234))
    assert first == second == (client.image, "tgs")
    assert client.catalog_calls == 1 and client.download_calls == 1
    assert store.gift_sticker(1234) == bytes(client.doc)
    # Кэш работает даже после отключения Telegram и перезапуска сервиса.
    class NoPool:
        async def first(self):
            raise RuntimeError("нет аккаунта")

    art = GiftArt(tmp_path, store, NoPool())
    assert await art.get(1234) == (client.image, "tgs")
    store.close()


async def test_saved_gift_reference_and_unavailable(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    client = FakeClient()
    store.remember_gift_sticker(1234, bytes(client.doc))
    art = GiftArt(tmp_path, store, FakePool(client))
    assert await art.get(1234) == (client.image, "tgs")
    assert client.catalog_calls == 0
    assert await art.get(9999) is None
    assert await art.get(9998) is None
    assert client.catalog_calls == 1  # отсутствующие подарки не запрашивают каталог каждый раз
    assert await art.get(0) is None
    store.close()


def test_art_endpoint_auth_and_binary(tmp_path, monkeypatch):
    settings = Settings(home=tmp_path, db_path=tmp_path / "api.db", sessions_dir=tmp_path / "sessions",
                        api_token="secret", auto_backup=False)
    app = create_app(settings)
    data = gzip.compress(b'{"v":"5.0"}')

    async def get_art(gift_id):
        return (data, "tgs") if gift_id == 1234 else None

    monkeypatch.setattr(app.state.hd.gift_art, "get", get_art)
    with TestClient(app) as client:
        assert client.get("/api/gifts/art/1234").status_code == 401
        response = client.get("/api/gifts/art/1234", headers={"X-Api-Token": "secret"})
        assert response.status_code == 200
        assert response.content == data
        assert response.headers["content-type"] == "application/x-tgsticker"
        assert client.get("/api/gifts/art/4321", headers={"X-Api-Token": "secret"}).status_code == 404


async def test_broken_tgs_cache_is_refetched(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    client = FakeClient()
    art = GiftArt(tmp_path, store, FakePool(client))
    art.directory.mkdir()
    (art.directory / "1234.tgs").write_bytes(b"\x1f\x8b\x08broken")
    assert await art.get(1234) == (client.image, "tgs")
    assert client.download_calls == 1
    store.close()


async def test_catalog_failure_has_a_cooldown(tmp_path):
    store = Store(tmp_path / "db.sqlite")

    class Offline(FakeClient):
        async def __call__(self, request):
            self.catalog_calls += 1
            raise RuntimeError("нет сети")

    client = Offline()
    art = GiftArt(tmp_path, store, FakePool(client))
    assert await art.get(1) is None
    assert await art.get(2) is None
    assert client.catalog_calls == 1
    store.close()
