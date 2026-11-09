"""Демо-данные: посмотреть, как всё выглядит, не подключаясь к Telegram.

Повторяет список из примера (115 чатов, 8 страниц) и добавляет правдоподобный
«хвост», чтобы были видны все страницы. Ничего не отправляет в сеть.
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any

from .db import Store
from .util import now_ts
from .util import tzinfo as get_tz

DEMO_TARGET = 7126917488

# (месяц, день, приватный, статус, название, сообщений)
DEMO_HEAD: list[tuple[int, int, bool, str, str, int]] = [
    (11, 9, False, "member", "дд", 301),
    (12, 10, True, "member", "Аkвариум кктека", 2750),
    (11, 24, True, "member", "NEFERPITU SeRvices", 1627),
    (11, 24, False, "member", "ꜰʟᴏᴡ ᴄʜᴀᴛ", 485),
    (11, 4, False, "member", "EN/RU|POCO X6 PRO", 47),
    (9, 14, False, "member", "Чaиковскии ☕", 1882),
    (9, 8, False, "member", "Чат общения", 5964),
    (9, 4, False, "member", "🤡 TROLLING|FLOW", 16000),
    (8, 29, False, "left", "Стак#эгоистов🥷", 2),
    (8, 25, False, "member", "Соники💙", 7),
    (8, 25, False, "member", "BFG | Общая беседа", 12),
    (8, 25, False, "left", "тролляки Flow чат", 202),
    (8, 24, True, "member", "Чат пубг метро", 5414),
    (8, 24, True, "member", "чат ℮бланов", 12),
    (8, 24, False, "member", "Ищю Парня Девушку", 490),
]

DEMO_TAILS = [
    "Мемы | Мемыч",
    "Флудилка",
    "Аниме чат",
    "Крипто чат",
    "Работа и подработка",
    "Игровой уголок",
    "Музыка 24/7",
    "Знакомства",
    "Кино и сериалы",
    "Технологии",
    "Дизайн и арт",
    "Питон и код",
    "Обои и скины",
    "Футбол",
    "Книжный клуб",
    "Учёба и ЕГЭ",
    "Путешествия",
    "Фотография",
    "Машины",
    "Кулинария",
]

DEMO_SUFFIXES = ("", " Flow", " | RU", " chat", " 2.0", " | чат", " ☕", " 💙", " 🥷", " | FLOW")

DEMO_PEERS: list[tuple[str, str | None, str]] = [
    ("Катя", "katya_m", "ну ты где там? 👀"),
    ("Макс", "max_flood", "го в доту вечером"),
    ("Дима", None, "скинь инфу по той теме"),
    ("Аня", "anya_art", "я набросала пару идей, глянешь?"),
    ("Слава", "slava_dev", "пофиксил, вроде работает"),
    ("Рита", "rita_chat", "созвон в 8?"),
    ("Тимур", "timur_gg", "ору, это было сильно"),
    ("Лиза", None, "спасибо, выручил"),
    ("Гоша", "gosha_fx", "курс опять скачет"),
    ("Настя", "nastya_ph", "фотки залила в чат"),
]


def seed_demo(store: Store, *, target_id: int = DEMO_TARGET, total: int = 115) -> dict[str, Any]:
    """Наливает демо-базу. Идемпотентно: повторный вызов не плодит дубли."""
    rnd = random.Random(1337)
    tz = "Europe/Moscow"
    today = datetime.now(get_tz(tz))
    ts_now = now_ts()

    session_pk = store.add_session(
        "demo",
        str(store.path.parent / "sessions" / "demo.session"),
        tg_user_id=target_id,
        username="demo_user",
        first_name="Demo",
        status="demo",
    )
    target = store.upsert_target(
        target_id,
        username="demo_user",
        first_name="Demo",
        last_name="Target",
        notes="демо-данные",
    )
    target_pk = int(target["id"])

    # Голова списка идёт ровно как в примере: сегодняшняя дата, затем прошлый год.
    rows: list[tuple[int, int, bool, str, str, int]] = list(DEMO_HEAD[: max(0, total)])
    while len(rows) < max(0, total):
        month = rnd.choice([5, 6, 7])
        day = rnd.randint(1, 28)
        title = rnd.choice(DEMO_TAILS) + rnd.choice(DEMO_SUFFIXES)
        rows.append((month, day, rnd.random() < 0.28, "member", title, rnd.randint(3, 9000)))

    group_pks: list[int] = []
    for order, (month, day, private, status, title, messages) in enumerate(rows):
        year = today.year if order == 0 else today.year - 1
        date = datetime(year, month, day, rnd.randint(0, 23), rnd.randint(0, 59), tzinfo=get_tz(tz))
        last_ts = int(date.timestamp())
        username = None if private else f"demo_{order}"
        info = {
            "tg_id": 9000000000 + order,
            "title": title,
            "username": username,
            "kind": "supergroup",
            "is_public": not private,
            "is_megagroup": True,
            "members_count": rnd.randint(120, 250_000),
            "messages_total": messages,
            "last_message_at": last_ts,
            "meta_checked_at": ts_now,
            "discovered_from": "demo",
            "discovered_by": "demo",
        }
        group_pk = store.upsert_group(**info)
        store.link_session_group(session_pk, group_pk, is_member=True)
        store.upsert_target_group(
            target_pk,
            group_pk,
            status=status,
            source="own_session",
            session="demo",
        )
        group_pks.append(group_pk)

    # активность: последние сообщения цели и связи с собеседниками
    if group_pks and store.activity_stats(target_pk)["messages"] == 0:
        peers: list[int] = []
        for offset, (first, username, _line) in enumerate(DEMO_PEERS):
            peers.append(
                store.upsert_peer(
                    8000000000 + offset,
                    username=username,
                    first_name=first,
                )
            )
        for index in range(90):
            peer_pk = peers[index % len(peers)] if index % 3 else None
            date = ts_now - rnd.randint(0, 21 * 86400)
            text = DEMO_PEERS[index % len(DEMO_PEERS)][2]
            if index % 4 == 0:
                text = f"@{DEMO_PEERS[(index + 3) % len(DEMO_PEERS)][0].lower()} {text}"
            store.add_message(
                target_pk,
                group_pks[index % len(group_pks)],
                700000 + index,
                date=date,
                text=text,
                reply_to_peer=(8000000000 + (index % len(peers))) if peer_pk else None,
                media="photo" if index % 9 == 0 else None,
                views=rnd.randint(20, 4000) if index % 5 == 0 else None,
            )
            if peer_pk:
                store.bump_edge(
                    target_pk,
                    peer_pk,
                    replies=1 if index % 2 else 0,
                    mentions=1 if index % 4 == 0 else 0,
                    ts=date,
                )

    store.set_meta("demo_seeded", "1")
    store.log_event("info", f"демо-база: {len(rows)} чатов", "demo")
    store.touch_target_scan(target_id)
    return {
        "target": target,
        "target_pk": target_pk,
        "groups": len(rows),
        "session": "demo",
    }


def is_demo_seeded(store: Store) -> bool:
    return store.get_meta("demo_seeded") == "1"


def clear_demo(store: Store) -> None:
    with store.tx() as conn:
        conn.execute("DELETE FROM sessions WHERE name = 'demo'")
        conn.execute("DELETE FROM targets WHERE notes = 'демо-данные'")
        conn.execute("DELETE FROM groups WHERE discovered_from = 'demo'")
        conn.execute("DELETE FROM peers WHERE tg_id BETWEEN 8000000000 AND 8999999999")
        conn.execute("DELETE FROM events WHERE source = 'demo'")
    store.set_meta("demo_seeded", "0")
