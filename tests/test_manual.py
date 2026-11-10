from __future__ import annotations

import pytest

from healdragg.manual import parse_batch, parse_token


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://t.me/memachh", ("username", "memachh")),
        ("http://t.me/memachh/", ("username", "memachh")),
        ("t.me/memachh", ("username", "memachh")),
        ("telegram.me/memachh", ("username", "memachh")),
        ("@beeeelogvardeyskaya", ("username", "beeeelogvardeyskaya")),
        ("beeeelogvardeyskaya", ("username", "beeeelogvardeyskaya")),
        ("https://t.me/+AbCdEfGhIjK", ("invite", "AbCdEfGhIjK")),
        ("https://t.me/joinchat/AbCdEfGhIjK", ("invite", "AbCdEfGhIjK")),
        ("tg://resolve?domain=memachh", ("username", "memachh")),
        ("tg://join?invite=AbCdEfGhIjK", ("invite", "AbCdEfGhIjK")),
        ("https://t.me/s/memachh", ("username", "memachh")),
        ("https://t.me/memachh/123", ("username", "memachh")),
        ("https://t.me/c/1234567890/12", ("id", "-1001234567890")),
        ("-1001234567890", ("id", "-1001234567890")),
    ],
)
def test_parse_token_ok(raw: str, expected: tuple[str, str]) -> None:
    assert parse_token(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "https://t.me/",
        "https://t.me/addstickers/cats",
        "https://t.me/share/url?url=https://example.com",
        "https://example.com/chat",
        "привет",
        "@имя",
    ],
)
def test_parse_token_invalid(raw: str) -> None:
    assert parse_token(raw) is None


def test_parse_batch_dedup_and_stats() -> None:
    text = """
    https://t.me/memachh
    @memachh
    t.me/beeeelogvardeyskaya
    https://t.me/+AbCdEfGhIjK
    не-ссылка
    """
    data = parse_batch(text)
    assert data["stats"]["total"] == 5
    assert data["stats"]["valid"] == 3
    assert data["stats"]["invalid"] == 1
    assert data["stats"]["duplicates"] == 1
    assert data["stats"]["invalid_percent"] == 20.0
    assert data["stats"]["usernames"] == 2
    assert data["stats"]["invites"] == 1
    assert [item["value"] for item in data["items"]][0] == "memachh"


def test_parse_batch_empty() -> None:
    data = parse_batch("   \n\n  ")
    assert data["stats"]["total"] == 0
    assert data["stats"]["invalid_percent"] == 0.0
    assert data["items"] == []
