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


def test_nft_profiles_requires_session(client: TestClient) -> None:
    response = client.post("/api/gifts/nft/profiles", headers=HEADERS, json={"limit": 10})
    assert response.status_code == 409
    assert "аккаунта" in response.text.lower()


def test_nft_filters_and_total(client: TestClient) -> None:
    store = client.app.state.hd.store
    store.upsert_nft_item(
        "PlushPepe-777", gift_id=777, num=777, title="Плюшевый Пепе",
        owner_tg_id=777000, owner_username="durov", owner_name="Павел",
        owner_address="EQwallet", on_chain=1, value_amount=100, value_currency="TON",
        value_usd=900, resell_amount=0, availability_issued=1, availability_total=9000,
        seen_at=1,
    )
    store.upsert_nft_item(
        "SwissWatch-888", gift_id=888, num=888, title="Часы",
        owner_tg_id=777001, owner_username="durov", owner_name="Павел",
        on_chain=0, value_amount=10, value_currency="TON", value_usd=50,
        resell_amount=75, availability_issued=1, availability_total=9000, seen_at=2,
    )

    page = client.get("/api/gifts/nft?q=durov&limit=5", headers=HEADERS).json()
    assert page["total"] >= 2
    assert page["total"] == len(page["rows"]) or page["total"] > len(page["rows"])

    resale = client.get("/api/gifts/nft?q=durov&resell=1", headers=HEADERS).json()
    assert all(row["resell_amount"] for row in resale["rows"])
    assert all("SwissWatch" in row["slug"] for row in resale["rows"])

    by_collection = client.get("/api/gifts/nft?collection=SwissWatch", headers=HEADERS).json()
    assert [row["slug"] for row in by_collection["rows"]] == ["SwissWatch-888"]

    by_id = client.get("/api/gifts/nft?q=777000", headers=HEADERS).json()
    assert [row["slug"] for row in by_id["rows"]] == ["PlushPepe-777"]

    csv_resale = client.get("/api/gifts/nft/export.csv?q=durov&resell=1", headers=HEADERS)
    assert csv_resale.status_code == 200
    assert "SwissWatch-888" in csv_resale.text
    assert "PlushPepe-777" not in csv_resale.text


def test_nft_csv_export(client: TestClient) -> None:
    response = client.get("/api/gifts/nft/export.csv?chain=wallet", headers=HEADERS)
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "slug;title" in response.text


def test_backup_create_download_restore(client: TestClient) -> None:
    created = client.post("/api/backup/create", headers=HEADERS).json()
    assert created["name"].endswith(".zip")
    assert created["size"] > 0
    assert created["counts"]["targets"] >= 1

    listed = client.get("/api/backup", headers=HEADERS).json()
    assert listed["latest"]["name"] == created["name"]

    download = client.get("/api/backup/download", headers=HEADERS)
    assert download.status_code == 200
    assert download.content[:2] == b"PK"

    restored = client.post("/api/backup/restore", headers=HEADERS, content=download.content)
    assert restored.status_code == 200
    assert restored.json()["counts"]["targets"] >= 1


def test_session_import_api(client: TestClient) -> None:
    import base64
    import os

    from telethon.crypto import AuthKey
    from telethon.sessions import SQLiteSession

    path = Path(client.app.state.hd.settings.home) / "upload-src.session"
    session = SQLiteSession(str(path))
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(os.urandom(256))
    session.save()
    payload = path.read_bytes()
    path.unlink()

    response = client.post(
        "/api/sessions/import",
        headers=HEADERS,
        json={
            "filename": "uploaded.session",
            "content_b64": base64.b64encode(payload).decode(),
        },
    )
    assert response.status_code == 200, response.text
    imported = response.json()["imported"]
    assert imported and imported[0]["name"] == "uploaded"

    bad = client.post(
        "/api/sessions/import",
        headers=HEADERS,
        json={"filename": "bad.session", "content_b64": base64.b64encode(b"nope").decode()},
    )
    assert bad.status_code == 400
    assert "сесси" in bad.text.lower()


def test_display_currency(client: TestClient) -> None:
    base = client.get("/api/settings/display", headers=HEADERS).json()
    assert base["currency"] == "RUB"
    assert base["rates"]["usd_rub"] > 0

    changed = client.post(
        "/api/settings/display",
        headers=HEADERS,
        json={"currency": "USD", "rates": {"usd_rub": 100, "ton_usd": 6}},
    ).json()
    assert changed["currency"] == "USD"
    assert changed["rates"]["usd_rub"] == 100
    assert changed["rates"]["ton_usd"] == 6
    assert changed["rates"]["star_usd"] > 0  # остальное не сбрасывается

    bad = client.post("/api/settings/display", headers=HEADERS, json={"currency": "BTC"})
    assert bad.status_code == 400
    assert "валюта" in bad.text.lower()


def test_graph_global(client: TestClient) -> None:
    store = client.app.state.hd.store
    target = store.upsert_target(555111, username="global_demo", access_hash=1)
    group_pk = store.upsert_group(999888, title="Общий чат", username="common")
    store.upsert_target_group(int(target["id"]), group_pk, status="unknown", source="search")
    store.add_message(int(target["id"]), group_pk, 1, date=1700000000, text="привет")
    peer_pk = store.upsert_peer(424242)
    store.bump_edge(int(target["id"]), peer_pk, replies=2, ts=1700000000)

    payload = client.get("/api/graph/global?targets=100&chats=100&peers=100", headers=HEADERS).json()
    ids = {node["id"] for node in payload["nodes"]}
    assert f"t{int(target['id'])}" in ids
    assert f"g{group_pk}" in ids
    assert f"p{peer_pk}" in ids
    kinds = {link["kind"] for link in payload["links"]}
    assert {"writes", "talks"} <= kinds
    assert payload["stats"]["targets"] >= 1


def test_live_activity_returns_feed_while_collection_runs(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import threading

    import healdragg.api as api_module

    store = client.app.state.hd.store  # type: ignore[attr-defined]
    monkeypatch.setattr(store, "session_names", lambda: ["main"])
    started = threading.Event()
    release = threading.Event()
    calls = []

    async def collect(*args, **kwargs):
        calls.append(1)
        started.set()
        await asyncio.to_thread(release.wait, 5)
        return {"messages": 0}

    monkeypatch.setattr(api_module, "collect_activity", collect)
    try:
        first = client.post(f"/api/targets/{DEMO_TARGET}/activity/live", headers=HEADERS)
        assert first.status_code == 200 and first.json()["rows"]
        assert started.wait(2)
        second = client.post(f"/api/targets/{DEMO_TARGET}/activity/live", headers=HEADERS)
        assert second.json()["skipped"] == "занято"
        assert len(calls) == 1
        assert client.post(f"/api/targets/{DEMO_TARGET}/activity/collect", headers=HEADERS).status_code == 409
    finally:
        release.set()


def test_gift_owner_and_history_endpoints(client: TestClient) -> None:
    store = client.app.state.hd.store  # type: ignore[attr-defined]
    store.add_gift(991, saved_id=1, gift_id=1, title="Мишка", owner_name="Аня")
    store.defer_gift_profile(992, "large", "Большой профиль", 1200)
    store.observe_nft_owner("Bear-1", 991, None, "Аня")
    store.observe_nft_owner("Bear-1", 992, "large", "Большой профиль")
    owners = client.get("/api/gifts/owners?q=992", headers=HEADERS).json()["rows"]
    assert owners[0]["deferred_count"] == 1200
    history = client.get("/api/gifts/nft/Bear-1/history", headers=HEADERS).json()["rows"]
    assert [row["owner_tg_id"] for row in history] == [991, 992]
    assert client.get("/api/gifts/nft/Bear-1/history").status_code == 401


def test_heavy_watch_requires_valid_session_and_cancels(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import threading

    import healdragg.api as api_module

    store = client.app.state.hd.store  # type: ignore[attr-defined]
    store.add_gift(991, saved_id=1, gift_id=1, title="Мишка")
    store.add_session("chosen", "/tmp/chosen.session", tg_user_id=9, status="active")
    endpoint = "/api/gifts/owners/991/heavy"
    assert client.post(endpoint, headers=HEADERS, json={"session": "missing"}).status_code == 400
    started = threading.Event()
    calls = []

    async def fake_crawl(*args, **kwargs):
        calls.append(kwargs)
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(api_module, "crawl_gifts", fake_crawl)
    response = client.post(endpoint, headers=HEADERS, json={"session": "chosen", "watch": True})
    assert response.status_code == 200
    assert started.wait(2)
    assert calls[0]["sessions"] == ["chosen"] and calls[0]["expected_owner"] == 991
    assert client.post(endpoint, headers=HEADERS, json={"session": "chosen"}).status_code == 409
    cancelled = client.post("/api/jobs/gifts%3Aheavy%3A991/cancel", headers=HEADERS)
    assert cancelled.status_code == 200 and cancelled.json()["ok"] is True


def test_gift_runs_and_issue_details(client: TestClient):
    store = client.app.state.hd.store  # type: ignore[attr-defined]
    old = store.create_gift_crawl("old", 8, 50, 100)
    store.gift_crawl_enqueue(old, 12, 1, "donor", 987654321)
    store.gift_crawl_finish_item(old, 12, "unavailable", "нет доступа")
    store.gift_crawl_status(old, "done")
    newer = store.create_gift_crawl("other", 8, 50, 100)
    store.gift_crawl_status(newer, "done")
    # Завершённые обходы без проблем не прячут более ранние незавершённые.
    runs = client.get("/api/gifts/runs?attention=true&limit=1", headers=HEADERS).json()["rows"]
    assert runs[0]["id"] == old
    assert client.get(f"/api/gifts/runs/{old}/issues").status_code == 401
    response = client.get(f"/api/gifts/runs/{old}/issues?limit=1", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["rows"][0] == {
        "tg_id": 12, "username": "donor", "level": 1, "state": "unavailable", "error": "нет доступа"
    }
    assert client.get("/api/gifts/runs/999999/issues", headers=HEADERS).status_code == 404


def test_resume_prioritizes_pending_before_retrying_failures(client: TestClient, monkeypatch):
    import asyncio

    async def hold_crawl(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr("healdragg.api.crawl_gifts", hold_crawl)
    store = client.app.state.hd.store  # type: ignore[attr-defined]
    store.add_session("active", "active.session")
    run_id = store.create_gift_crawl("1", 8, 1, 100)
    store.gift_crawl_enqueue(run_id, 1, 0)
    store.gift_crawl_finish_item(run_id, 1, "unavailable", "попробовать позже")
    store.gift_crawl_enqueue(run_id, 2, 1)
    store.gift_crawl_status(run_id, "paused")
    response = client.post(f"/api/gifts/runs/{run_id}/resume", headers=HEADERS)
    assert response.status_code == 200
    counts = store.gift_crawl_counts(run_id)
    assert counts["pending"] == 1 and counts["unavailable"] == 1
    assert store.gift_crawl_next(run_id)["tg_id"] == 2
    assert client.post(f"/api/jobs/gifts:run:{run_id}/cancel", headers=HEADERS).status_code == 200

    store.gift_crawl_finish_item(run_id, 2, "done")
    assert client.post(f"/api/gifts/runs/{run_id}/resume", headers=HEADERS).status_code == 200
    assert store.gift_crawl_counts(run_id)["pending"] == 1
    assert store.gift_crawl_next(run_id)["tg_id"] == 1


def test_target_pagination_search_and_lookup(client: TestClient) -> None:
    store = client.app.state.hd.store
    for i in range(125):
        store.upsert_target(100_000 + i, username=f"user{i}", first_name=f"Имя {i}")
    store.upsert_target(100_000, username="oldest", first_name="Алексей", last_name="Петров")
    response = client.get("/api/targets/page?limit=20", headers=HEADERS)
    assert response.status_code == 200
    first = response.json()
    assert len(first["rows"]) == 20
    ids = [item["tg_user_id"] for item in first["rows"]]
    cursor = first["next_before"]
    # Updating an old row must not move it ahead of the cursor and lose it.
    store.update_target_profile(100_000, bio="updated between pages")
    store.upsert_target(900_000, username="new_after_start")
    while cursor is not None:
        page = client.get(f"/api/targets/page?limit=20&before={cursor}", headers=HEADERS).json()
        ids.extend(item["tg_user_id"] for item in page["rows"])
        cursor = page["next_before"]
    assert len(ids) == len(set(ids)) == 126  # 125 plus the demo target
    assert 100_000 in ids and 900_000 not in ids
    assert client.get("/api/targets/page?q=АЛЕКСЕЙ%20пет", headers=HEADERS).json()["rows"][0]["tg_user_id"] == 100_000
    assert client.get("/api/targets/page?q=%40OLDEST", headers=HEADERS).json()["rows"][0]["tg_user_id"] == 100_000
    assert client.get("/api/targets/page?q=%25", headers=HEADERS).json()["rows"] == []
    assert client.get("/api/targets/lookup?q=%40oldest", headers=HEADERS).json()["tg_user_id"] == 100_000
    assert client.get("/api/targets/lookup?q=100000", headers=HEADERS).json()["tg_user_id"] == 100_000
    assert client.get("/api/targets/lookup?q=missing", headers=HEADERS).status_code == 404
    assert client.get("/api/targets/page?before=bad", headers=HEADERS).status_code == 400
    assert client.get("/api/targets/page?before=999999999999999999999999", headers=HEADERS).status_code == 400
    assert client.get("/api/targets/page?q=" + "a" * 129, headers=HEADERS).status_code == 400
    assert client.get("/api/targets/page").status_code == 401
    store.reopen()
    assert client.get("/api/targets/page?q=алексей", headers=HEADERS).json()["rows"][0]["tg_user_id"] == 100_000


def test_target_links_paginate_in_database(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    store = client.app.state.hd.store

    def no_full_load(*args, **kwargs):
        raise AssertionError("must not load the entire target group list")

    monkeypatch.setattr(store, "all_target_groups", no_full_load)
    first = client.get(f"/api/targets/{DEMO_TARGET}/links?limit=7", headers=HEADERS).json()
    second = client.get(f"/api/targets/{DEMO_TARGET}/links?limit=7&offset=7", headers=HEADERS).json()
    assert first["total"] == second["total"] == 40
    assert len(first["rows"]) == len(second["rows"]) == 7
    assert {row["tg_id"] for row in first["rows"]}.isdisjoint(row["tg_id"] for row in second["rows"])
    assert client.get("/api/targets/lookup?q=" + "9" * 100, headers=HEADERS).status_code == 404


def test_profile_gifts_and_gift_search(client: TestClient) -> None:
    store = client.app.state.hd.store
    store.add_gift(DEMO_TARGET, saved_id=800, gift_id=1, title="Сердце", from_tg_id=12)
    store.add_gift(DEMO_TARGET, saved_id=801, gift_id=1, title="Скрытый", from_tg_id=13, name_hidden=True)
    store.add_gift(14, saved_id=802, gift_id=1, title="Сердце", from_tg_id=DEMO_TARGET)
    store.add_gift(15, saved_id=803, gift_id=1, title="Скрытый отправитель", from_tg_id=DEMO_TARGET, name_hidden=True)
    store.upsert_nft_item("PlushPepe-123", title="Плюшевый Пепе", gift_id=12, owner_tg_id=14)
    base = f"/api/targets/{DEMO_TARGET}/gifts"
    incoming = client.get(base + "?direction=received&limit=1", headers=HEADERS).json()
    assert len(incoming["rows"]) == 1 and incoming["has_more"]
    sent = client.get(base + "?direction=sent", headers=HEADERS).json()
    assert [row["owner_tg_id"] for row in sent["rows"]] == [14]
    assert client.get(base + "?direction=bad", headers=HEADERS).status_code == 400
    assert client.get("/api/targets/999999999/gifts", headers=HEADERS).status_code == 404
    assert len(client.get("/api/search/gifts?q=СЕРДЦЕ", headers=HEADERS).json()["gifts"]) == 2
    assert client.get("/api/search/gifts?q=ПЕПЕ", headers=HEADERS).json()["nft"][0]["slug"] == "PlushPepe-123"
    assert client.get("/api/search/gifts?q=", headers=HEADERS).status_code == 400
    assert client.get("/api/search/gifts?q=pepe").status_code == 401


def test_live_nft_search_validation_and_lookup(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    app = client.app.state.hd
    app.store.add_session("live", str(app.settings.sessions_dir / "live.session"), status="active")
    called = []

    async def resolve(pool, store, slug):
        called.append(slug)
        return {"slug": slug, "title": "Plush Pepe", "owner_tg_id": None}

    monkeypatch.setattr("healdragg.api.resolve_unique_gift", resolve)
    assert client.post("/api/search/nft", json={"slug": "../../etc/passwd"}, headers=HEADERS).status_code == 400
    assert client.post("/api/search/nft", json={"slug": "PlushPepe-0"}, headers=HEADERS).status_code == 400
    result = client.post("/api/search/nft", json={"slug": "PlushPepe-123"}, headers=HEADERS)
    assert result.status_code == 200 and result.json()["item"]["slug"] == "PlushPepe-123"
    assert called == ["PlushPepe-123"]
    assert client.post("/api/search/nft", json={"slug": "PlushPepe-123"}).status_code == 401


def test_open_profile_preserves_saved_and_does_not_start_watch(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    app = client.app.state.hd
    existing = client.post("/api/profiles/open", json={"query": str(DEMO_TARGET)}, headers=HEADERS)
    assert existing.status_code == 200
    assert existing.json()["tg_user_id"] == DEMO_TARGET
    assert client.post("/api/profiles/open", json={"query": ""}, headers=HEADERS).status_code == 400
    app.store.add_session("live", str(app.settings.sessions_dir / "live.session"), status="active")

    async def resolve(pool, store, query, *, watch_enabled=True):
        assert query == "@newperson" and not watch_enabled
        return store.upsert_target(1234567, username="newperson", watch_enabled=watch_enabled)

    monkeypatch.setattr("healdragg.api.resolve_and_store_target", resolve)
    created = client.post("/api/profiles/open", json={"query": "@newperson"}, headers=HEADERS)
    assert created.status_code == 200
    assert app.store.get_target(1234567)["watch_enabled"] == 0


def test_profile_gift_collect_records_owner_and_refresh_flag(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    app = client.app.state.hd
    app.store.add_session("live", str(app.settings.sessions_dir / "live.session"), status="active")
    from healdragg import api

    async def fake_crawl(*args, **kwargs):
        return {"gifts": 0, "new": 0}

    monkeypatch.setattr(api, "crawl_gifts", fake_crawl)
    result = client.post(f"/api/targets/{DEMO_TARGET}/gifts/collect", headers=HEADERS)
    assert result.status_code == 200
    run_id = result.json()["state"]["run_id"]
    run = app.store.gift_crawl(run_id)
    assert run["expected_owner"] == DEMO_TARGET
    assert run["refresh_known"] == 1
    assert run["query"] == "demo_user"


def test_search_people_finds_local_observations_without_hidden_senders(client: TestClient) -> None:
    store = client.app.state.hd.store
    for tg_id in range(2001, 2031):
        store.upsert_peer(tg_id, username=f"peer{tg_id}", first_name=f"Марина {tg_id}")
    store.add_gift(3001, saved_id=1, gift_id=1, title="Bear", owner_name="Марина Владелец",
                   from_tg_id=3002, from_name="Марина Даритель")
    store.add_gift(3003, saved_id=2, gift_id=1, title="Bear", owner_name="Другой",
                   from_tg_id=3004, from_name="Марина Скрытая", name_hidden=True)
    store.upsert_nft_item("Bear-1", owner_tg_id=3005, owner_name="Марина NFT")
    store.upsert_target(2001, first_name="Марина 2001")
    found = []
    before = None
    while True:
        path = "/api/search/people?q=МАРИНА&limit=7" + (f"&before={before}" if before else "")
        response = client.get(path, headers=HEADERS)
        assert response.status_code == 200
        page = response.json()
        found.extend(page["rows"])
        before = page["next_before"]
        if before is None:
            break
    ids = [row["tg_id"] for row in found]
    assert len(ids) == len(set(ids)) == 32
    assert {3001, 3002, 3005, 2030} <= set(ids)
    assert 3004 not in ids and 2001 not in ids
    assert client.get("/api/search/people?q=@PEER2030", headers=HEADERS).json()["rows"][0]["tg_id"] == 2030
    assert client.get("/api/search/people?q=%25", headers=HEADERS).json()["rows"] == []
    assert client.get("/api/search/people?q=марина&before=-1", headers=HEADERS).status_code == 400
    assert client.get("/api/search/people?q=марина").status_code == 401

    # The source is a local observation, not proof of current Telegram identity.
    response = client.post("/api/profiles/open", headers=HEADERS, json={"query": "3002"})
    assert response.status_code == 200 and response.json()["tg_user_id"] == 3002
    assert store.get_target(3002)["watch_enabled"] == 0
    assert client.get("/api/search/people?q=марина", headers=HEADERS).json()["rows"]
    hidden = client.post("/api/profiles/open", headers=HEADERS, json={"query": "3004"})
    assert hidden.status_code == 409 and store.get_target(3004) is None


def test_profile_gifts_cursor_stable_while_dates_change(client: TestClient) -> None:
    store = client.app.state.hd.store
    for index in range(75):
        store.add_gift(DEMO_TARGET, saved_id=10000 + index, gift_id=100, title="Gift", date=1000 + index)
    base = f"/api/targets/{DEMO_TARGET}/gifts?direction=received&limit=11"
    first = client.get(base, headers=HEADERS).json()
    ids = [r["id"] for r in first["rows"]]
    cursor = first["next_before"]
    # Changing dates and inserting a new observation must not shift the cursor.
    store.add_gift(DEMO_TARGET, saved_id=10000, gift_id=100, title="Gift", date=999999)
    store.add_gift(DEMO_TARGET, saved_id=20000, gift_id=100, title="New", date=2000)
    while cursor is not None:
        page = client.get(base + f"&before={cursor}", headers=HEADERS).json()
        ids.extend(r["id"] for r in page["rows"])
        cursor = page["next_before"]
    assert len(ids) == len(set(ids)) == 75
    assert store.list_gifts(DEMO_TARGET, limit=1)[0]["date"] == 999999
    assert client.get(base + "&before=xyz", headers=HEADERS).status_code == 400
    assert client.get(base + "&before=1&offset=1", headers=HEADERS).status_code == 400
    assert client.get(base + "&before=999999999999999999999999", headers=HEADERS).status_code == 400


def test_alert_notification_snapshot_uses_id_not_count_or_date(client: TestClient) -> None:
    store = client.app.state.hd.store
    store.mark_alerts_read()
    old = store.add_alert("test", "old", ts=9999999999)
    first = client.get("/api/alerts?unread=true&limit=1", headers=HEADERS).json()["notification"]
    assert first["id"] == old and first["unread"] == 1 and first["stream_id"]
    store.mark_alerts_read([old])
    new = store.add_alert("test", "new", ts=1)
    payload = client.get("/api/alerts?unread=true&limit=1", headers=HEADERS).json()
    assert payload["notification"]["id"] == new
    assert payload["notification"]["title"] == "new"
    assert payload["notification"]["unread"] == 1
    assert payload["notification"]["stream_id"] == first["stream_id"]
    # Оставшаяся старая запись с будущей датой не должна скрыть новый ID.
    store.add_alert("test", "future", ts=9999999999)
    latest = store.add_alert("test", "latest", ts=2)
    payload = client.get("/api/alerts?unread=true&limit=1", headers=HEADERS).json()
    assert payload["rows"][0]["title"] == "future"
    assert payload["notification"]["id"] == latest
    assert client.get("/api/alerts").status_code == 401


def test_business_bot_requires_auth_confirmation_and_existing_session(client: TestClient) -> None:
    assert client.get("/api/sessions/demo/business-bot").status_code == 401
    assert client.post("/api/sessions/demo/business-bot", headers=HEADERS, json={}).status_code == 400
    assert client.post("/api/sessions/missing/business-bot", headers=HEADERS, json={"confirm": True}).status_code == 404


def test_waiting_gift_jobs_identify_blocker_and_release_on_stop(client: TestClient, monkeypatch) -> None:
    import asyncio
    import threading

    started_a = threading.Event()
    started_b = threading.Event()

    async def hold_crawl(*args, **kwargs):
        (started_a if args[3] == "one" else started_b).set()
        await asyncio.Event().wait()

    monkeypatch.setattr("healdragg.api.crawl_gifts", hold_crawl)
    store = client.app.state.hd.store
    store.add_session("active", "active.session")
    first = client.post("/api/gifts/crawl", headers=HEADERS, json={"query": "one"}).json()
    assert started_a.wait(2)
    second = client.post("/api/gifts/crawl", headers=HEADERS, json={"query": "two"}).json()
    jobs = client.get("/api/jobs", headers=HEADERS).json()
    waiting = next(j for j in jobs if j["key"] == second["key"])
    assert waiting["state"]["waiting_for"] == first["key"]
    assert not started_b.is_set()
    assert client.post(f"/api/jobs/{first['key']}/cancel", headers=HEADERS).json()["ok"]
    assert started_b.wait(2)
    assert client.post(f"/api/jobs/{second['key']}/cancel", headers=HEADERS).json()["ok"]
    assert client.app.state.hd.gift_lock_owner is None
    assert not client.app.state.hd.gift_lock.locked()


def test_archive_gift_run_preserves_queue_and_requires_stop(client: TestClient, monkeypatch) -> None:
    import asyncio

    async def hold(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr("healdragg.api.crawl_gifts", hold)
    store = client.app.state.hd.store
    store.add_session("active", "active.session")
    run = store.create_gift_crawl("one", 2, 20, 100)
    store.gift_crawl_enqueue(run, 42, 1, "test")
    store.gift_crawl_status(run, "paused")
    assert client.post(f"/api/gifts/runs/{run}/archive", headers=HEADERS, json={"archived": True}).json()["ok"]
    assert not client.get("/api/gifts/runs?attention=true", headers=HEADERS).json()["rows"]
    assert client.get("/api/gifts/runs?archived=true", headers=HEADERS).json()["rows"][0]["id"] == run
    assert client.post(f"/api/gifts/runs/{run}/resume", headers=HEADERS).status_code == 409
    assert client.post(f"/api/gifts/runs/{run}/archive", headers=HEADERS, json={"archived": False}).json()["ok"]
    assert store.gift_crawl_next(run)["tg_id"] == 42
    assert client.post(f"/api/gifts/runs/{run}/resume", headers=HEADERS).status_code == 200
    assert client.post(f"/api/gifts/runs/{run}/archive", headers=HEADERS, json={"archived": True}).status_code == 409
    assert client.post(f"/api/jobs/gifts:run:{run}/cancel", headers=HEADERS).json()["ok"]
    assert store.gift_crawl_next(run)["tg_id"] == 42


def test_offer_session_is_listed_but_not_selected_for_scanning(client: TestClient) -> None:
    store = client.app.state.hd.store
    store.add_session("writer", "writer.session", purpose="offers")
    rows = client.get("/api/sessions", headers=HEADERS).json()
    assert next(row for row in rows if row["name"] == "writer")["purpose"] == "offers"
    assert "writer" not in store.session_names()
    assert client.post("/api/gifts/chain", headers=HEADERS, json={}).status_code == 409
    assert client.post("/api/session/start", headers=HEADERS, json={"purpose": "invalid"}).status_code == 400


def test_offer_candidates_api_is_read_only_and_validated(client: TestClient) -> None:
    store = client.app.state.hd.store
    store.upsert_nft_item("Sample-9", owner_tg_id=123, owner_username="seller", value_usd=1000)
    assert client.post("/api/offers/candidates", json={}).status_code == 401
    data = client.post("/api/offers/candidates", headers=HEADERS, json={"query": "@seller"})
    assert data.status_code == 200
    assert [r["slug"] for r in data.json()["rows"]] == ["Sample-9"]
    assert client.post("/api/offers/candidates", headers=HEADERS, json={"limit": True}).status_code == 400
    assert client.post("/api/offers/candidates", headers=HEADERS, json={"gender": []}).status_code == 400
    assert client.post("/api/offers/candidates", headers=HEADERS, json={"send": True}).status_code == 400


def test_nft_profiles_job_exposes_inner_progress_and_completion(client: TestClient, monkeypatch) -> None:
    import asyncio
    import threading

    started = threading.Event()
    release = threading.Event()

    async def crawl(*args, **kwargs):
        await kwargs["reporter"]({"phase": "жду страницу", "done": 2, "total": 10, "found": 3,
                                  "owners_done": 0, "owners_total": 4, "errors": 1,
                                  "last_error": "недоступен"})
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return {"profiles": 3, "owners": 1, "owners_total": 4, "new": 4,
                "errors": 1, "last_error": "недоступен"}

    monkeypatch.setattr("healdragg.api.crawl_nft_profiles", crawl)
    store = client.app.state.hd.store
    store.add_session("scan", "scan.session")
    store.upsert_nft_item("Bear-1", owner_tg_id=99, ownership_observed=True)
    assert client.post("/api/gifts/nft/profiles", headers=HEADERS, json={"limit": 10}).status_code == 200
    assert started.wait(2)
    job = next(j for j in client.get("/api/jobs", headers=HEADERS).json() if j["kind"] == "giftprofiles")
    assert job["state"]["phase"] == "жду страницу" and job["state"]["done"] == 2
    assert job["state"]["last_error"] == "недоступен" and job["state"]["owners_total"] == 4
    release.set()
    for _ in range(20):
        job = next(j for j in client.get("/api/jobs", headers=HEADERS).json() if j["kind"] == "giftprofiles")
        if job["finished"]:
            break
        import time
        time.sleep(0.01)
    assert job["finished"] and job["state"]["phase"] == "готово"
    assert job["state"]["done"] == 3 and job["state"]["total"] == 10
    assert job["state"]["found"] == 4 and job["state"]["errors"] == 1


def test_offer_preview_is_authenticated_and_never_sends(client: TestClient):
    store = client.app.state.hd.store
    store.upsert_nft_item("Offer-1", owner_tg_id=17, owner_username="seller17", value_usd=1000)
    params = {"currency": "star", "usd_per_unit": "0.02", "markup_percent": "10",
              "max_per_offer": 1000, "total_budget": 1000, "max_offers": 5}
    assert client.post("/api/offers/preview", json=params).status_code == 401
    data = client.post("/api/offers/preview", json=params, headers=HEADERS)
    assert data.status_code == 200
    assert data.json()["rows"][0]["amount"] == 550
    assert client.post("/api/offers/preview", json={**params, "send": True}, headers=HEADERS).status_code == 400
    assert not client.app.state.hd.jobs


def test_offer_verify_api_is_protected_and_no_send_route(client: TestClient, monkeypatch):
    from healdragg import offer_check

    called = []

    async def check(store, pool, body):
        called.append(body)
        return {"owner_matches": False}

    monkeypatch.setattr(offer_check, "check", check)
    body = {"session": "writer", "slug": "Bear-1", "seller_id": 42}
    assert client.post("/api/offers/verify", json=body).status_code == 401
    assert client.post("/api/offers/verify", headers=HEADERS, json=body).json() == {"owner_matches": False}
    assert called == [body]
    assert client.post("/api/offers/send", headers=HEADERS, json={}).status_code == 404


def test_offer_batch_api_start_stop_and_recovery(client: TestClient, monkeypatch):
    import asyncio

    from healdragg import offer_check

    async def wait_forever(store, pool, body):
        await asyncio.Event().wait()

    monkeypatch.setattr(offer_check, "check", wait_forever)
    store = client.app.state.hd.store
    store.add_session("writer", "writer.session", purpose="offers")
    store.upsert_nft_item("Bear-1", owner_tg_id=1, owner_username="seller1", value_usd=1000)
    body = {"session": "writer", "plan": {"currency": "star", "usd_per_unit": "0.02",
            "markup_percent": "0", "max_per_offer": 1000, "total_budget": 1000, "max_offers": 1}}
    assert client.get("/api/offers/batch").status_code == 401
    assert client.post("/api/offers/batch", json=body).status_code == 401
    started = client.post("/api/offers/batch", headers=HEADERS, json=body)
    assert started.status_code == 200 and started.json()["run"]["status"] == "running"
    duplicate = client.post("/api/offers/batch", headers=HEADERS, json=body)
    assert duplicate.status_code == 409
    assert client.get("/api/offers/batch", headers=HEADERS).json()["run"]["status"] == "running"
    stopped = client.post("/api/offers/batch/stop", headers=HEADERS, json={}).json()["run"]
    assert stopped["status"] == "stopped"
    assert stopped["rows"][0]["status"] == "pending"
    assert client.post("/api/offers/batch/stop", headers=HEADERS, json={}).json()["run"]["status"] == "stopped"
    assert client.post("/api/offers/send", headers=HEADERS, json={}).status_code == 404


def test_offer_delivery_api_requires_confirmation_and_can_stop(client: TestClient, monkeypatch):
    import asyncio

    from healdragg import offer_delivery

    async def wait_forever(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(offer_delivery, '_open_account', wait_forever)
    store = client.app.state.hd.store
    store.add_session('writer', 'writer.session', purpose='offers')
    store.upsert_nft_item('Bear-1', owner_tg_id=1, owner_username='seller1', value_usd=1000)
    body = {'session': 'writer', 'language': 'ru', 'plan': {'currency': 'star', 'usd_per_unit': '0.02',
            'markup_percent': '0', 'max_per_offer': 1000, 'total_budget': 1000, 'max_offers': 1}}
    assert client.get('/api/offers/delivery').status_code == 401
    assert client.post('/api/offers/delivery/prepare', json=body).status_code == 401
    prepared = client.post('/api/offers/delivery/prepare', headers=HEADERS, json=body).json()['run']
    assert prepared['rows'][0]['command'] == '/createoffer @seller1 https://t.me/nft/Bear-1 500 star ru'
    assert client.post('/api/offers/delivery/start', headers=HEADERS, json={'run_id': prepared['id']}).status_code == 400
    assert not client.get('/api/offers/delivery', headers=HEADERS).json()['history']
    started = client.post('/api/offers/delivery/start', headers=HEADERS, json={'run_id': prepared['id'], 'confirm': True})
    assert started.status_code == 200 and started.json()['run']['status'] == 'running'
    assert client.post('/api/offers/delivery/start', headers=HEADERS, json={'run_id': prepared['id'], 'confirm': True}).status_code == 409
    assert client.post('/api/offers/delivery/stop', headers=HEADERS, json={}).json()['run']['status'] == 'stopped'
    assert not client.get('/api/offers/delivery', headers=HEADERS).json()['history']


def test_restore_blocks_delivery_until_explicit_reconciliation(client: TestClient):
    download = client.get('/api/backup/download', headers=HEADERS)
    assert client.post('/api/backup/restore', headers=HEADERS, content=download.content).status_code == 200
    assert client.get('/api/offers/delivery', headers=HEADERS).json()['restore_blocked'] is True
    assert client.post('/api/offers/delivery/ack', headers=HEADERS, json={}).status_code == 400
    assert client.post('/api/offers/delivery/ack', headers=HEADERS, json={'confirm': True}).json()['ok']
    assert client.get('/api/offers/delivery', headers=HEADERS).json()['restore_blocked'] is False


def test_delivery_history_survives_restore_of_older_backup(client: TestClient):
    store = client.app.state.hd.store
    store.add_session('writer', 'writer.session', purpose='offers')
    store.upsert_nft_item('Bear-1', owner_tg_id=1, owner_username='seller1', value_usd=1000)
    backup = client.get('/api/backup/download?fresh=1', headers=HEADERS).content
    ledger = client.app.state.hd.offer_ledger
    body = {'session': 'writer', 'language': 'ru', 'plan': {'currency': 'star', 'usd_per_unit': '0.02',
            'markup_percent': '0', 'max_per_offer': 1000, 'total_budget': 1000, 'max_offers': 1}}
    run = ledger.prepare(store, body)
    ledger.start(run['id'])
    attempt, _ = ledger.reserve(run, run['rows'][0], 777)
    ledger.outcome(run['id'], 0, attempt, 'telegram_ack', 'ok', 12)
    ledger.finish(run['id'], 'done')
    assert client.post('/api/backup/restore', headers=HEADERS, content=backup).status_code == 200
    state = client.get('/api/offers/delivery', headers=HEADERS).json()
    assert state['restore_blocked'] is True and state['history'][0]['id'] == attempt
    assert state['history'][0]['status'] == 'telegram_ack'
