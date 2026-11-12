"""Остановка Android-движка безопасна даже во время старта сервера."""
from __future__ import annotations

import importlib.util
import socket
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route


@pytest.fixture
def engine(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "android/app/src/main/python/engine_main.py"
    spec = importlib.util.spec_from_file_location("test_android_engine_main", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_prepare_logging", lambda home: None)
    monkeypatch.setattr(module, "_apply_env", lambda *args: None)
    monkeypatch.setattr("healdragg.config.load_settings", lambda: None)
    yield module
    assert module.stop(5), "engine thread leaked"


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_stop_while_app_is_being_created_does_not_leave_a_server(engine, monkeypatch, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    async def close():
        closed.set()

    def create_app(_settings):
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(state=SimpleNamespace(hd=SimpleNamespace(close=close)))

    monkeypatch.setattr("healdragg.api.create_app", create_app)
    assert engine.start(str(tmp_path), "1", "hash", "token", free_port()) == "started"
    assert entered.wait(5)
    assert not engine.stop(0)
    assert engine.status()["stopping"]
    assert engine.start(str(tmp_path), "1", "hash", "token", 8080) == "stopping"
    release.set()
    wait_for(lambda: not engine.status()["running"])
    assert closed.is_set()
    assert engine.service_state() == "stopped"


def test_ready_only_after_server_start_and_clean_restart(engine, monkeypatch, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    @asynccontextmanager
    async def lifespan(_app):
        entered.set()
        release.wait(5)
        yield
        closed.set()

    async def health(_request):
        return PlainTextResponse("ok")

    monkeypatch.setattr("healdragg.api.create_app", lambda _: Starlette(
        routes=[Route("/health", health)], lifespan=lifespan,
    ))
    port = free_port()
    assert engine.start(str(tmp_path), "1", "hash", "token", port) == "started"
    assert entered.wait(5)
    assert engine.service_state() == "starting"
    assert not engine.status()["ready"]
    release.set()
    wait_for(lambda: engine.service_state() == "ready")
    assert engine.start(str(tmp_path), "1", "hash", "token", port) == "already"
    assert engine.stop(5)
    assert closed.is_set()
    assert engine.service_state() == "stopped"
    assert engine.start(str(tmp_path), "1", "hash", "token", port) == "started"
    wait_for(lambda: engine.service_state() == "ready")
    assert engine.stop(5)


def test_port_failure_is_reported_not_ready(engine, monkeypatch, tmp_path):
    app = Starlette()
    monkeypatch.setattr("healdragg.api.create_app", lambda _: app)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        assert engine.start(str(tmp_path), "1", "hash", "token", port) == "started"
        wait_for(lambda: not engine.status()["running"])
        assert not engine.status()["ready"]
        assert engine.service_state().startswith("error: ")


def test_stop_during_lifespan_startup_still_runs_shutdown(engine, monkeypatch, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    @asynccontextmanager
    async def lifespan(_app):
        entered.set()
        release.wait(5)
        yield
        closed.set()

    monkeypatch.setattr("healdragg.api.create_app", lambda _: Starlette(lifespan=lifespan))
    assert engine.start(str(tmp_path), "1", "hash", "token", free_port()) == "started"
    assert entered.wait(5)
    assert not engine.stop(0)
    release.set()
    wait_for(lambda: not engine.status()["running"])
    assert closed.is_set()
    assert engine.service_state() == "stopped"


def test_creation_failure_is_visible_and_a_later_start_can_recover(engine, monkeypatch, tmp_path):
    def fail(_settings):
        raise RuntimeError("ошибка базы")

    monkeypatch.setattr("healdragg.api.create_app", fail)
    port = free_port()
    assert engine.start(str(tmp_path), "1", "hash", "token", port) == "started"
    wait_for(lambda: not engine.status()["running"])
    assert "ошибка базы" in engine.service_state()
    monkeypatch.setattr("healdragg.api.create_app", lambda _: Starlette())
    assert engine.start(str(tmp_path), "1", "hash", "token", port) == "started"
    wait_for(lambda: engine.service_state() == "ready")
    assert not engine.status()["error"]
    assert engine.stop(5)
