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


def test_intel_endpoint(client: TestClient) -> None:
    assert client.get("/api/intel?name=Анна").status_code == 401
    row = client.get("/api/intel?name=Анна", headers=HEADERS).json()["rows"][0]
    assert row["gender"] == "female"
    assert row["country"] == "RU"
    rows = client.get("/api/intel?names=Mehmet,张伟", headers=HEADERS).json()["rows"]
    assert [r["country"] for r in rows] == ["TR", "CN"]
    assert client.get("/api/intel", headers=HEADERS).status_code == 400


def test_gifts_empty_and_bad_requests(client: TestClient) -> None:
    data = client.get("/api/gifts", headers=HEADERS).json()
    assert data["rows"] == []
    assert data["summary"]["total"] == 0
    graph = client.get("/api/gifts/graph", headers=HEADERS).json()
    assert graph["nodes"] == [] and graph["edges"] == []
    assert (
        client.post("/api/gifts/crawl", json={}, headers=HEADERS).status_code == 400
    )
    assert client.get("/api/gifts", headers=HEADERS).status_code == 200


def test_target_payload_has_profile_fields(client: TestClient) -> None:
    target = client.get("/api/targets", headers=HEADERS).json()[0]
    assert "photo" in target and target["photo"]["present"] is False
    assert "intel" in target and "gender" in target["intel"]
    assert "gifts" in target
    assert (
        client.get(f"/api/targets/{DEMO_TARGET}/photo", headers=HEADERS).status_code == 404
    )


def test_catalog_parse_endpoint(client: TestClient) -> None:
    response = client.post(
        "/api/catalog/parse",
        headers=HEADERS,
        json={"text": "https://t.me/memachh\n@beeeelogvardeyskaya\nне-ссылка"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["stats"]["valid"] == 2
    assert data["stats"]["invalid"] == 1
    assert data["stats"]["invalid_percent"] == 33.3
    assert isinstance(data["sessions"], list)
    assert data["items"][0]["link"] == "https://t.me/memachh"


def test_catalog_parse_and_import_validation(client: TestClient) -> None:
    assert client.post("/api/catalog/parse", headers=HEADERS, json={}).status_code == 400
    assert (
        client.post("/api/catalog/parse", headers=HEADERS, json={"text": "x" * 200_001}).status_code
        == 400
    )
    assert client.post("/api/catalog/import", headers=HEADERS, json={}).status_code == 400
    bad = client.post(
        "/api/catalog/import", headers=HEADERS, json={"text": "просто текст без ссылок"}
    )
    assert bad.status_code == 400
    # валидный список без аккаунта-сканера → понятная ошибка про вход
    assert (
        client.post("/api/catalog/import", headers=HEADERS, json={"text": "@memachh"}).status_code
        == 409
    )


def test_catalog_update_and_delete(client: TestClient) -> None:
    rows = client.get("/api/catalog?size=1", headers=HEADERS).json()["rows"]
    tg_id = rows[0]["tg_id"]
    updated = client.post(
        f"/api/catalog/{tg_id}/edit",
        headers=HEADERS,
        json={"title": "Новое имя", "username": "@newlink"},
    )
    assert updated.status_code == 200
    group = updated.json()["group"]
    assert group["title"] == "Новое имя"
    assert group["username"] == "newlink"
    assert group["is_public"] == 1
    assert group["link"] == "https://t.me/newlink"
    assert (
        client.post(
            "/api/catalog/999999999/edit", headers=HEADERS, json={"title": "x"}
        ).status_code
        == 404
    )
    assert client.delete(f"/api/catalog/{tg_id}", headers=HEADERS).json()["ok"] is True
    assert client.delete(f"/api/catalog/{tg_id}", headers=HEADERS).status_code == 404


def test_gift_refresh_requires_session(client: TestClient) -> None:
    response = client.post("/api/gifts/refresh", headers=HEADERS, json={})
    assert response.status_code == 409
    assert "аккаунта" in response.text.lower()


def test_live_activity_without_session(client: TestClient) -> None:
    response = client.post(f"/api/targets/{DEMO_TARGET}/activity/live", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert "rows" in data and "stats" in data
    assert data.get("skipped")


def test_nft_inventory_endpoints(client: TestClient) -> None:
    data = client.get("/api/gifts/nft?chain=wallet&limit=20", headers=HEADERS).json()
    assert set(data) >= {"rows", "summary", "owners", "collections", "chain"}
    assert data["chain"] == "wallet"
    assert {"total", "on_chain", "hosted", "owners", "capacity", "scanned"} <= set(
        data["summary"]
    )
    owners = client.get("/api/gifts/nft/owners?chain=telegram", headers=HEADERS).json()
    assert "rows" in owners and "summary" in owners
    assert client.get("/api/gifts/nft").status_code == 401


def test_gift_chain_requires_session(client: TestClient) -> None:
    response = client.post("/api/gifts/chain", headers=HEADERS, json={"requests": 100})
    assert response.status_code == 409
    assert "аккаунта" in response.text.lower()
