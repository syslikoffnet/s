"""Цены в валюте приложения: по умолчанию рубли."""

from __future__ import annotations

from healdragg.money import DEFAULT_RATES, convert, format_price

RATES = dict(DEFAULT_RATES)


def test_ton_to_rub() -> None:
    # 1 TON = 5 $ = 460 ₽ при курсе 92
    assert convert(1, "TON", "RUB", RATES) == 460


def test_stars_to_rub() -> None:
    value = convert(985, "STARS", "RUB", RATES)
    assert value is not None
    assert 1150 < value < 1200  # 985 * 0.013 * 92 ≈ 1178


def test_usd_to_rub_and_back() -> None:
    assert convert(10, "USD", "RUB", RATES) == 920
    assert convert(920, "RUB", "USD", RATES) == 10
    assert convert(50, "TON", "USD", RATES) == 250


def test_unknown_source_is_none() -> None:
    assert convert(10, "XYZ", "RUB", RATES) is None


def test_format_price_defaults_to_rubles() -> None:
    label = format_price(10, "TON", currency="RUB", rates=RATES)
    assert label == "4 600 ₽"


def test_format_price_stars_rounds() -> None:
    label = format_price(98500, "STARS", currency="STARS", rates=RATES)
    assert label == "98 500 ★"


def test_nft_estimate_uses_usd_cents_and_does_not_guess_currency() -> None:
    from healdragg.money import enrich_row

    row = {"slug": "Gift-1", "value_amount": 123456, "value_currency": "INR", "value_usd": 1234}
    assert enrich_row(row, currency="USD", rates=RATES)["price_display"] == "12,34 $"
    unknown = {"slug": "Gift-2", "value_amount": 123456, "value_currency": "INR"}
    assert "price_display" not in enrich_row(unknown, currency="USD", rates=RATES)
    usd = {"slug": "Gift-3", "value_amount": 1234, "value_currency": "USD"}
    assert enrich_row(usd, currency="USD", rates=RATES)["price_display"] == "12,34 $"
