from __future__ import annotations

from datetime import datetime

from healdragg.textfmt import (
    ChatRow,
    progress_bar,
    render_account_chats,
    render_catalog,
    render_stats,
    to_plain,
)
from healdragg.util import Page, fmt_day, truncate, tzinfo

TZ = "Europe/Moscow"


def ts(day: int, month: int, year: int = 2025, hour: int = 12) -> int:
    return int(datetime(year, month, day, hour, tzinfo=tzinfo(TZ)).timestamp())


def test_page_math() -> None:
    page = Page.build(115, 1, 15)
    assert (page.pages, page.offset, page.has_prev, page.has_next) == (8, 0, False, True)
    assert Page.build(115, 8, 15).has_next is False
    assert Page.build(0, 5, 15).page == 1
    assert Page.build(115, 99, 15).page == 8


def test_page_window() -> None:
    assert Page.build(115, 1, 15).window() == [1, 2, 3, -1, 8]
    assert Page.build(100, 5, 15).window() == [1, 2, 3, 4, 5, 6, 7]
    assert Page.build(300, 10, 15).window() == [1, -1, 8, 9, 10, 11, 12, -1, 20]


def test_day_format() -> None:
    assert fmt_day(ts(9, 11), TZ) == "09 нояб."
    assert fmt_day(ts(1, 5), TZ) == "01 мая"
    assert fmt_day(None, TZ) == "—"


def test_truncate_wide_emoji() -> None:
    assert truncate("коротко", 15) == "коротко"
    assert truncate("abcdefghijklmnopqrst", 10) == "abcdefghij.."
    assert truncate("🤡🤡🤡🤡🤡🤡🤡🤡🤡🤡", 6) == "🤡🤡🤡.."


def test_chat_row_markers() -> None:
    assert ChatRow(title="x", status="admin", is_public=True).markers == "👮🏻‍♂️"
    assert ChatRow(title="x", status="member", is_public=False).markers == "🔒"
    assert ChatRow(title="x", status="left", is_public=True).markers == "✖️"
    assert ChatRow(title="x", status="member", is_public=True).markers == ""
    assert ChatRow(title="x", status="admin", is_public=False).markers == "👮🏻‍♂️🔒"


def test_exact_account_chats_format() -> None:
    rows = [
        {
            "title": "дд",
            "tg_id": 1,
            "is_public": True,
            "status": "member",
            "last_message_at": ts(9, 11),
            "messages_total": 301,
        },
        {
            "title": "Аkвариум кктека",
            "tg_id": 2,
            "is_public": False,
            "status": "member",
            "last_message_at": ts(10, 12, 2024),
            "messages_total": 2750,
        },
        {
            "title": "Стак#эгоистов🥷",
            "tg_id": 3,
            "is_public": True,
            "status": "left",
            "last_message_at": ts(29, 8, 2024),
            "messages_total": 2,
        },
    ]
    text = render_account_chats(rows, target_id=7126917488, page=Page.build(115, 1, 15), tz=TZ)
    expected_head = (
        "ㅤЧаты аккаунта 7126917488.\n"
        "👮🏻‍♂️-админ, 🔒-приватный, ✖️-вышел\n"
        "Посл. сοобщ - чат (всего сообщений)\n\n"
    )
    assert text.startswith(expected_head)
    assert " 09 нояб. дд (301)" in text
    assert " 🔒10 дек. Аkвариум кктека (2750)" in text
    assert " ✖️29 авг. Стак#эгоистов🥷 (2)" in text
    assert text.endswith("Всего 115, cтρаница 1 из 8")


def test_plain_mode_and_html_escape() -> None:
    rows = [
        {
            "title": "<b>чат</b>",
            "is_public": True,
            "status": "member",
            "last_message_at": ts(1, 1, 2025),
            "messages_total": 1,
        }
    ]
    plain = render_account_chats(rows, target_id=5, page=Page.build(1, 1, 15), homoglyph=False)
    assert "Посл. сообщ - чат (всего сообщений)" in plain
    assert "страница 1 из 1" in plain
    escaped = render_account_chats(rows, target_id=5, page=Page.build(1, 1, 15), escape_html=True)
    assert "&lt;b&gt;чат&lt;/b&gt;" in escaped
    assert to_plain("<b>жирный</b> &amp; <code>код</code>") == "жирный & код"


def test_catalog_and_stats() -> None:
    rows = [
        {"title": "Чат общения", "username": "chat", "members_count": 500, "last_message_at": ts(1, 1)}
    ]
    text = render_catalog(rows, page=Page.build(1, 1, 15))
    assert "База групп" in text
    assert "@chat" in text
    stats = render_stats({"groups": 1234, "public_groups": 1000, "db_bytes": 1024 * 1024})
    assert "1 234" in stats
    assert "1.0 МБ" in stats
    assert progress_bar(5, 10, width=10) == "▓▓▓▓▓░░░░░"
