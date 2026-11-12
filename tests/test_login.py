from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from healdragg.config import Settings
from healdragg.db import Store
from healdragg.login import LoginError, LoginManager


@pytest.fixture()
def manager(tmp_path: Path):
    settings = Settings(
        home=tmp_path,
        db_path=tmp_path / "login.db",
        sessions_dir=tmp_path / "sessions",
        api_token="token",
    )
    settings.ensure_dirs()
    store = Store(settings.db_path)
    login = LoginManager(settings, store)
    yield login
    asyncio.run(login.close())
    store.close()


def test_start_needs_phone(manager: LoginManager) -> None:
    with pytest.raises(LoginError, match="номер"):
        asyncio.run(manager.start("   "))


def test_start_needs_api_credentials(manager: LoginManager) -> None:
    with pytest.raises(LoginError, match="API_ID"):
        asyncio.run(manager.start("+79990000000"))


def test_unknown_flow_errors(manager: LoginManager) -> None:
    with pytest.raises(LoginError, match="истёк"):
        asyncio.run(manager.submit_code("nope", "123456"))
    with pytest.raises(LoginError, match="истёк"):
        asyncio.run(manager.submit_password("nope", "secret"))


def test_cancel_unknown_is_safe(manager: LoginManager) -> None:
    asyncio.run(manager.cancel("nope"))


def test_expired_flow_is_rejected(manager: LoginManager) -> None:
    flow = _fake_flow(flow_id="old", created_at=0.0)
    manager._flows["old"] = flow  # noqa: SLF001 — проверяем именно TTL

    with pytest.raises(LoginError, match="истёк"):
        asyncio.run(manager.submit_code("old", "12345"))
    assert "old" not in manager._flows  # noqa: SLF001


def test_cancel_disconnects_flow(manager: LoginManager) -> None:
    flow = _fake_flow(flow_id="live", created_at=_now())
    manager._flows["live"] = flow  # noqa: SLF001
    asyncio.run(manager.cancel("live"))

    assert flow.client.disconnected is True
    assert "live" not in manager._flows  # noqa: SLF001


class _FakeClient:
    def __init__(self) -> None:
        self.disconnected = False

    async def disconnect(self) -> None:
        self.disconnected = True


def _now() -> float:
    import time

    return time.time()


def _fake_flow(flow_id: str, created_at: float):
    from healdragg.login import Flow

    return Flow(
        id=flow_id,
        name="main",
        phone="+79990000000",
        created_at=created_at,
        client=_FakeClient(),  # type: ignore[arg-type]
    )


def test_rejects_path_in_session_name(manager: LoginManager) -> None:
    manager.settings.api_id = 123
    manager.settings.api_hash = "a" * 32
    for name in ("../outside", "/tmp/account", "dir\\account", ".."):
        with pytest.raises(LoginError, match="имя сессии"):
            asyncio.run(manager.start("+79990000000", name))


def test_login_saves_offers_purpose(manager: LoginManager, monkeypatch) -> None:
    from types import SimpleNamespace

    from healdragg import login as login_module

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def send_code_request(self, phone):
            return SimpleNamespace(phone_code_hash="hash")

        async def sign_in(self, **kwargs):
            pass

        async def get_me(self):
            return SimpleNamespace(id=12345, username="writer", first_name="Writer", phone=None)

    monkeypatch.setattr(login_module, "TelegramClient", Client)
    manager.settings.api_id = 123
    manager.settings.api_hash = "a" * 32

    async def login():
        flow = await manager.start("+79990000000", purpose="offers")
        await manager.submit_code(flow["id"], "12345")
    asyncio.run(login())
    assert manager.store.get_session("main")["purpose"] == "offers"
    assert manager.store.session_names() == []
