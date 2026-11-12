from __future__ import annotations

from healdragg.alerts import (
    detect_left_chats,
    detect_new_chats,
    detect_new_messages,
    format_alert,
    render_digest,
)
from healdragg.db import Store


def _target(store: Store) -> dict:
    return store.upsert_target(42, username="target_user")


def test_new_chat_alerts_skip_first_run_and_dedupe(store: Store) -> None:
    target = _target(store)
    first = detect_new_chats(store, target)
    assert first == []  # первая проверка только ставит точку отсчёта

    group_pk = store.upsert_group(1001, title="Новый чат", username="newchat")
    store.upsert_target_group(int(target["id"]), group_pk, status="member", source="probe")
    created = detect_new_chats(store, target)
    assert len(created) == 1
    assert "Новый чат" in created[0]["title"]

    # повторный прогон не плодит дубль
    assert detect_new_chats(store, target) == []
    assert store.count_alerts() == 1


def test_left_chat_alert(store: Store) -> None:
    target = _target(store)
    group_pk = store.upsert_group(1002, title="Стак#эгоистов", username="stack")
    store.upsert_target_group(int(target["id"]), group_pk, status="left", source="own_session")
    detect_left_chats(store, target)  # baseline
    store.upsert_target_group(int(target["id"]), group_pk, status="left", source="probe")
    created = detect_left_chats(store, target)
    assert len(created) == 1
    assert "Вышел" in store.list_alerts()[0]["title"]


def test_message_alert_from_activity(store: Store) -> None:
    target = _target(store)
    group_pk = store.upsert_group(1003, title="Чат", username="chat")
    assert detect_new_messages(store, target) == []  # baseline

    store.add_message(int(target["id"]), group_pk, 1, date=1_760_000_000, text="привет")
    created = detect_new_messages(store, target)
    assert len(created) == 1
    assert created[0]["kind"] == "message"
    assert "привет" in store.list_alerts()[0]["body"]


def test_alerts_read_and_pending(store: Store) -> None:
    target = _target(store)
    first = store.add_alert("message", "тест", body="тело", target_pk=int(target["id"]))
    assert first
    assert store.count_alerts() == 1
    pending = store.pending_alerts()
    assert len(pending) == 1
    store.mark_alert_sent(int(pending[0]["id"]))
    assert store.pending_alerts() == []

    assert store.mark_alerts_read() == 1
    assert store.count_alerts() == 0
    assert store.list_alerts(unread_only=True) == []


def test_digest_escapes_html(store: Store) -> None:
    target = _target(store)
    alert = store.add_alert(
        "new_chat",
        "Новый <b>чат</b>",
        body="@username <script>",
        target_pk=int(target["id"]),
    )
    assert alert
    rows = store.list_alerts()
    text = format_alert(rows[0])
    assert "&lt;b&gt;" in text
    assert "&lt;script&gt;" in text
    digest = render_digest(rows)
    assert "Алерты" in digest
    assert render_digest([]) .startswith("🔔")
