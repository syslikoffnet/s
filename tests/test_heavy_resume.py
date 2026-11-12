"""Большие профили восстанавливаются после сбоя, но не после кнопки «Стоп»."""
from __future__ import annotations

import asyncio
import threading

import pytest
from starlette.testclient import TestClient

from healdragg.api import create_app
from healdragg.config import Settings
from healdragg.db import Store
from healdragg.util import now_ts

HEADERS = {"X-Api-Token": "secret"}


def settings_for(tmp_path):
    return Settings(home=tmp_path, db_path=tmp_path / "api.db", sessions_dir=tmp_path / "sessions",
                    api_token="secret", auto_backup=False)


@pytest.mark.parametrize("watch", [False, True])
def test_heavy_task_recovers_and_user_stop_is_final(tmp_path, monkeypatch, watch):
    settings = settings_for(tmp_path)
    entered = threading.Event()
    calls = []

    async def crawl(*args, **kwargs):
        calls.append(kwargs)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("healdragg.api.crawl_gifts", crawl)
    app = create_app(settings)
    app.state.hd.store.add_session("chosen", "chosen.session")
    app.state.hd.store.add_gift(991, saved_id=1, gift_id=1, title="Подарок", owner_username="owner")
    with TestClient(app) as client:
        response = client.post("/api/gifts/owners/991/heavy", headers=HEADERS,
                               json={"session": "chosen", "watch": watch, "interval_seconds": 60})
        assert response.status_code == 200
        assert entered.wait(2)
        assert calls[-1]["sessions"] == ["chosen"]
        assert calls[-1]["expected_owner"] == 991
    # Закрытие движка имитирует аварийное прерывание: настройки остаются активными.
    store = Store(settings.db_path)
    assert store.heavy_task(991)["status"] == "running"
    store.close()

    entered.clear()
    app = create_app(settings)
    with TestClient(app) as client:
        assert entered.wait(2)
        assert calls[-1]["sessions"] == ["chosen"] and calls[-1]["expected_owner"] == 991
        assert client.get("/api/gifts/owners/991/heavy", headers=HEADERS).json()["task"]["watch"] == int(watch)
        assert client.post("/api/jobs/gifts%3Aheavy%3A991/cancel", headers=HEADERS).json()["ok"]
        assert client.get("/api/gifts/owners/991/heavy", headers=HEADERS).json()["task"]["status"] == "stopped"

    entered.clear()
    with TestClient(create_app(settings)) as client:
        assert not any(j["key"] == "gifts:heavy:991" for j in client.get("/api/jobs", headers=HEADERS).json())
        assert not entered.is_set()


def test_watch_preserves_next_poll_time_across_restart(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    entered = threading.Event()
    calls = []

    async def crawl(*args, **kwargs):
        calls.append(kwargs)
        entered.set()
        return {"results": [{"complete": True}], "gifts": 20, "new": 0}

    monkeypatch.setattr("healdragg.api.crawl_gifts", crawl)
    app = create_app(settings)
    app.state.hd.store.add_session("chosen", "chosen.session")
    app.state.hd.store.add_gift(10, saved_id=1, gift_id=1, title="Подарок")
    with TestClient(app) as client:
        assert client.post("/api/gifts/owners/10/heavy", headers=HEADERS,
                           json={"session": "chosen", "watch": True, "interval_seconds": 60}).status_code == 200
        assert entered.wait(2)
    store = Store(settings.db_path)
    assert store.heavy_task(10)["next_at"] > now_ts()
    store.close()
    calls.clear()
    with TestClient(create_app(settings)) as client:
        task = next(j for j in client.get("/api/jobs", headers=HEADERS).json() if j["key"] == "gifts:heavy:10")
        assert not task["finished"]
        assert not calls
        assert client.post("/api/jobs/gifts%3Aheavy%3A10/cancel", headers=HEADERS).json()["ok"]


def test_removing_chosen_account_stops_task_with_explanation(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    entered = threading.Event()

    async def crawl(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("healdragg.api.crawl_gifts", crawl)
    app = create_app(settings)
    app.state.hd.store.add_session("chosen", "chosen.session")
    app.state.hd.store.add_gift(11, saved_id=1, gift_id=1, title="Подарок")
    with TestClient(app) as client:
        client.post("/api/gifts/owners/11/heavy", headers=HEADERS, json={"session": "chosen", "watch": True})
        assert entered.wait(2)
        assert client.delete("/api/sessions/chosen", headers=HEADERS).status_code == 200
        task = client.get("/api/gifts/owners/11/heavy", headers=HEADERS).json()["task"]
        assert task["status"] == "error" and "удалён" in task["last_error"]
        assert client.get("/api/gifts/owners/11/heavy").status_code == 401


def test_changed_session_identity_is_not_reused(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    entered = threading.Event()

    async def crawl(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("healdragg.api.crawl_gifts", crawl)
    app = create_app(settings)
    app.state.hd.store.add_session("chosen", "chosen.session", tg_user_id=44)
    app.state.hd.store.add_gift(15, saved_id=1, gift_id=1, title="Подарок")
    with TestClient(app) as client:
        assert client.post("/api/gifts/owners/15/heavy", headers=HEADERS,
                           json={"session": "chosen", "watch": True}).status_code == 200
        assert entered.wait(2)
    store = Store(settings.db_path)
    store.add_session("chosen", "chosen.session", tg_user_id=55)
    store.close()
    entered.clear()
    with TestClient(create_app(settings)) as client:
        task = client.get("/api/gifts/owners/15/heavy", headers=HEADERS).json()["task"]
        assert task["status"] == "error" and "изменился" in task["last_error"]
        assert not entered.is_set()
        errors = client.get("/api/gifts/heavy/tasks", headers=HEADERS).json()["rows"]
        assert errors[0]["owner_tg_id"] == 15
        assert client.post("/api/gifts/owners/15/heavy/dismiss", headers=HEADERS).status_code == 200
        assert client.get("/api/gifts/heavy/tasks", headers=HEADERS).json()["rows"] == []


def test_watch_network_failure_keeps_schedule_and_error(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    entered = threading.Event()

    async def crawl(*args, **kwargs):
        entered.set()
        raise RuntimeError("нет сети")

    monkeypatch.setattr("healdragg.api.crawl_gifts", crawl)
    app = create_app(settings)
    app.state.hd.store.add_session("chosen", "chosen.session")
    app.state.hd.store.add_gift(16, saved_id=1, gift_id=1, title="Подарок")
    with TestClient(app) as client:
        assert client.post("/api/gifts/owners/16/heavy", headers=HEADERS,
                           json={"session": "chosen", "watch": True, "interval_seconds": 60}).status_code == 200
        assert entered.wait(2)
        task = client.get("/api/gifts/owners/16/heavy", headers=HEADERS).json()["task"]
        assert task["status"] == "running" and task["last_error"] == "нет сети"
        assert task["next_at"] > now_ts()
        assert client.post("/api/gifts/owners/16/heavy/dismiss", headers=HEADERS).status_code == 409
        assert client.post("/api/jobs/gifts%3Aheavy%3A16/cancel", headers=HEADERS).json()["ok"]


def test_changed_username_stops_instead_of_retrying_another_person(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    entered = threading.Event()

    async def crawl(*args, **kwargs):
        entered.set()
        raise ValueError("Имя пользователя больше не принадлежит указанному владельцу")

    monkeypatch.setattr("healdragg.api.crawl_gifts", crawl)
    app = create_app(settings)
    app.state.hd.store.add_session("chosen", "chosen.session", tg_user_id=5)
    app.state.hd.store.add_gift(18, saved_id=1, gift_id=1, title="Подарок", owner_username="oldname")
    with TestClient(app) as client:
        response = client.post("/api/gifts/owners/18/heavy", headers=HEADERS,
                               json={"session": "chosen", "watch": True})
        assert response.status_code == 200
        assert entered.wait(2)
        task = client.get("/api/gifts/owners/18/heavy", headers=HEADERS).json()["task"]
        assert task["status"] == "error" and "не принадлежит" in task["last_error"]
