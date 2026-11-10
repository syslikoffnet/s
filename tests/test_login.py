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
