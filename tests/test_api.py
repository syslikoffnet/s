from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from healdragg.api import create_app
from healdragg.config import Settings
from healdragg.demo import DEMO_TARGET, seed_demo
from healdragg.tg import SessionPool, chat_to_group, normalize_identifier, participant_status


@pytest.fixture()
def client(tmp_path: Path):
    settings = Settings(
        home=tmp_path,
        db_path=tmp_path / "api.db",
        sessions_dir=tmp_path / "sessions",
        api_token="secret",
    )
    settings.ensure_dirs()
    app = create_app(settings)
    seed_demo(app.state.hd.store, total=40)
    with TestClient(app) as test_client:
        yield test_client


HEADERS = {"X-Api-Token": "secret"}


def test_health_open(client: TestClient) -> None:
    data = client.get("/api/health").json()
    assert data["ok"] is True
    assert "version" in data


def test_auth_required(client: TestClient) -> None:
    assert client.get("/api/stats").status_code == 401
    assert client.get("/api/stats?token=wrong").status_code == 401
    assert client.get("/api/stats", headers=HEADERS).status_code == 200
    assert client.get("/api/stats?token=secret").status_code == 200


def test_targets_and_groups(client: TestClient) -> None:
    targets = client.get("/api/targets", headers=HEADERS).json()
    assert targets and targets[0]["tg_user_id"] == DEMO_TARGET
    assert targets[0]["stats"]["total"] == 40

    data = client.get(
        f"/api/targets/{DEMO_TARGET}/groups?page=1&size=15", headers=HEADERS
    ).json()
    assert data["total"] == 40
    assert data["pages"] == 3
    assert len(data["rows"]) == 15
    assert "Чаты аккаунта" in data["text"]
    assert "Всего 40" in data["text"]
    assert all("link" in row for row in data["rows"])
    assert any(row["link"] for row in data["rows"])

    page3 = client.get(f"/api/targets/{DEMO_TARGET}/groups?page=3&size=15", headers=HEADERS).json()
    assert len(page3["rows"]) == 10

    profile = client.get(f"/api/targets/{DEMO_TARGET}", headers=HEADERS).json()
    assert profile["username"] == "demo_user"
    assert "activity" in profile and profile["activity"]["messages"] >= 0

    links = client.get(f"/api/targets/{DEMO_TARGET}/links", headers=HEADERS).json()
    assert links["total"] == 40
    assert links["offset"] == 0 and links["has_more"] is False
    assert any(row["link"] for row in links["rows"])

    first = client.get(f"/api/targets/{DEMO_TARGET}/links?limit=5", headers=HEADERS).json()
    second = client.get(f"/api/targets/{DEMO_TARGET}/links?limit=5&offset=5", headers=HEADERS).json()
    assert len(first["rows"]) == 5 and len(second["rows"]) == 5
    assert first["has_more"] and second["offset"] == 5
    assert {row["title"] for row in first["rows"]}.isdisjoint({row["title"] for row in second["rows"]})


def test_catalog_and_find(client: TestClient) -> None:
    data = client.get("/api/catalog?page=1&size=10", headers=HEADERS).json()
    assert data["total"] == 40
    assert len(data["rows"]) == 10
    assert "База групп" in data["text"]

    found = client.get("/api/find?q=demo", headers=HEADERS).json()
    assert found["total"] > 0


def test_sessions(client: TestClient) -> None:
    rows = client.get("/api/sessions", headers=HEADERS).json()
    assert rows[0]["name"] == "demo"


def test_web_and_manifest(client: TestClient) -> None:
    html = client.get("/")
    assert html.status_code == 200
    assert "Healdragg" in html.text
    manifest = client.get("/manifest.webmanifest").json()
    assert manifest["short_name"] == "Healdragg"


def test_job_requires_sessions(client: TestClient) -> None:
    # демо-сессия не считается рабочей (status=demo)
    response = client.post(f"/api/targets/{DEMO_TARGET}/scan", headers=HEADERS)
    assert response.status_code == 409


def test_telethon_helpers() -> None:
    assert normalize_identifier("https://t.me/durov?x=1") == "durov"
    assert normalize_identifier("@durov") == "durov"
    assert normalize_identifier(" t.me/durov/ ") == "durov"
    assert participant_status(type("ChannelParticipantAdmin", (), {})()) == "admin"
    assert participant_status(type("ChannelParticipantCreator", (), {})()) == "creator"
    assert participant_status(type("ChannelParticipant", (), {})()) == "member"
    assert chat_to_group(object()) is None


def test_session_pool_without_credentials(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, db_path=tmp_path / "x.db", sessions_dir=tmp_path / "s")
    from healdragg.db import Store

    store = Store(settings.db_path)
    pool = SessionPool(settings, store)
    assert pool.connected() == []
    store.close()


def test_graph_and_activity_endpoints(client: TestClient) -> None:
    graph = client.get(f"/api/targets/{DEMO_TARGET}/graph", headers=HEADERS)
    assert graph.status_code == 200
    payload = graph.json()
    assert payload["target"]["tg_id"] == DEMO_TARGET
    assert any(node["type"] == "chat" for node in payload["nodes"])

    activity = client.get(f"/api/targets/{DEMO_TARGET}/activity", headers=HEADERS).json()
    assert activity["stats"]["messages"] > 0
    assert activity["rows"] and activity["rows"][0]["text"]

    script = client.get("/graph.js")
    assert script.status_code == 200
    assert "HealdraggGraph" in script.text


def test_alerts_endpoints(client: TestClient) -> None:
    from healdragg.alerts import run_detection

    store = client.app.state.hd.store  # type: ignore[attr-defined]
    target = store.get_target(DEMO_TARGET)
    run_detection(store, target, messages=True)

    data = client.get("/api/alerts?limit=10", headers=HEADERS).json()
    assert "rows" in data and "unread" in data
    read = client.post("/api/alerts/read", json={}, headers=HEADERS).json()
    assert read["ok"] is True


def test_web_panel_markup(client: TestClient) -> None:
    html = client.get("/").text
    for marker in ('id="linksMore"', 'class="tabbar"', 'name="theme-color"', 'id="tabBell"'):
        assert marker in html
    assert "github.com/syslikoffnet/s.git" not in html


def test_api_has_no_pydantic() -> None:
    """Панель должна ставиться на Termux без сборки pydantic-core."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "healdragg" / "api.py").read_text()
    for banned in ("fastapi", "pydantic"):
        assert banned not in source.lower()


def test_login_endpoints_validation(client: TestClient) -> None:
    assert client.post("/api/session/start", json={}).status_code == 401
    assert (
        client.post("/api/session/start", json={}, headers=HEADERS).status_code == 400
    )
    assert (
        client.post(
            "/api/session/code", json={"id": "nope", "code": "123"}, headers=HEADERS
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/session/password", json={"id": "nope", "password": "x"}, headers=HEADERS
        ).status_code
        == 400
    )
    assert (
        client.post("/api/session/nope/cancel", headers=HEADERS).json()["ok"] is True
    )


def test_delete_session(client: TestClient) -> None:
    assert client.delete("/api/sessions/main", headers=HEADERS).status_code == 404
    store = client.app.state.hd.store
    store.add_session("main", "/tmp/main.session", username="main_user", status="active")
    assert client.delete("/api/sessions/main", headers=HEADERS).status_code == 200
    assert "main" not in store.session_names(only_active=False)
