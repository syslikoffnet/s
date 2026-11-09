from __future__ import annotations

from healdragg.bot import keyboards
from healdragg.textfmt import ChatRow, fit_telegram, render_account_chats
from healdragg.util import Page


def flatten(markup) -> list[list[tuple[str, str]]]:
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


def test_home_has_groups_button() -> None:
    markup = keyboards.home(has_sessions=True, has_groups=True, target_id=7126917488)
    texts = [text for row in flatten(markup) for text, _ in row]
    assert "👁 Группы" in texts
    assert any(data == "g:7126917488:1" for row in flatten(markup) for _, data in row)


def test_home_empty_db_invites_bootstrap() -> None:
    markup = keyboards.home(has_sessions=True, has_groups=False)
    texts = [text for row in flatten(markup) for text, _ in row]
    assert "🚀 Собрать базу" in texts


def test_pager_first_and_middle_pages() -> None:
    first = flatten(keyboards.pager(42, Page.build(115, 1, 15)))
    assert first[0] == [("· 1/8 ·", "noop"), ("➡ 2", "g:42:2")]
    middle = flatten(keyboards.pager(42, Page.build(115, 2, 15)))
    assert middle[0] == [("1 ⬅", "g:42:1"), ("· 2/8 ·", "noop"), ("➡ 3", "g:42:3")]
    numbers = [text for row in middle[1:] for text, _ in row]
    assert "…" in numbers and "8" in numbers


def test_last_page_has_no_next() -> None:
    last = flatten(keyboards.pager(42, Page.build(115, 8, 15)))
    assert all(text != "➡ 9" for row in last for text, _ in row)
    assert any(text == "7 ⬅" for row in last for text, _ in row)


def test_target_menu_without_data() -> None:
    markup = keyboards.target_menu(7, has_data=False)
    texts = [text for row in flatten(markup) for text, _ in row]
    assert "👁 Группы" in texts
    assert "🚀 Собрать базу" in texts


def test_cancel_and_scan_done() -> None:
    cancel = flatten(keyboards.cancel_job("scan", "42"))
    assert cancel[0][0][1] == "job:cancel:scan:42"
    boot = flatten(keyboards.cancel_job("boot"))
    assert boot[0][0][1] == "job:cancel:boot"
    done = [text for row in flatten(keyboards.scan_done(42)) for text, _ in row]
    assert "👁 Группы" in done


def test_fit_telegram_trims() -> None:
    text = "\n".join(["x" * 100] * 100)
    trimmed = fit_telegram(text, 500)
    assert len(trimmed) <= 500
    assert trimmed.endswith("…")


def test_many_rows_fit_telegram() -> None:
    rows = [
        ChatRow(title=f"чат {i}", last_message_at=1_760_000_000 + i * 3600, messages_total=i)
        for i in range(15)
    ]
    text = render_account_chats(rows, target_id=1, page=Page.build(115, 1, 15))
    assert len(fit_telegram(text)) < 4096


def test_targets_list_keyboard() -> None:
    rows = [
        {"tg_user_id": 1, "username": "alpha", "first_name": "A"},
        {"tg_user_id": 2, "username": None, "first_name": "B"},
    ]
    markup = keyboards.targets_list(rows, active_id=2)
    buttons = flatten(markup)
    datas = [data for row in buttons for _, data in row]
    assert "p:1" in datas and "p:2" in datas
    texts = [text for row in buttons for text, _ in row]
    assert any(text.startswith("· ") for text in texts)
    assert "🏠 Меню" in texts


def test_home_has_targets_shortcut() -> None:
    markup = keyboards.home(has_sessions=True, has_groups=True, target_id=5)
    texts = [text for row in flatten(markup) for text, _ in row]
    assert "🎯 Цели" in texts


def test_links_page_keyboard() -> None:
    rows = [
        {"title": "Публичный чат", "link": "https://t.me/pubchat"},
        {"title": "Приватный канал", "link": "https://t.me/c/1234567890/1"},
        {"title": "Basic-группа", "link": None},
    ]
    markup = keyboards.links_page(rows, 42, Page.build(45, 2, 15))
    buttons = [[(b.text, b.url, b.callback_data) for b in row] for row in markup.inline_keyboard]
    urls = [url for row in buttons for _, url, _ in row if url]
    assert urls == ["https://t.me/pubchat", "https://t.me/c/1234567890/1"]
    texts = [text for row in buttons for text, _, _ in row]
    assert any(text.startswith("16.") for text in texts)
    datas = [data for row in buttons for _, _, data in row if data]
    assert "lnk:42:1" in datas
    assert "g:42:2" in datas


def test_links_page_keyboard_empty() -> None:
    markup = keyboards.links_page([], 42, Page.build(0, 1, 15))
    texts = [b.text for row in markup.inline_keyboard for b in row]
    assert "Ссылок пока нет" in texts
