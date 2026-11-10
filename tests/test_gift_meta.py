"""Разбор цены подарка: звёзды не должны выглядеть как TON."""

from __future__ import annotations

from types import SimpleNamespace

from healdragg.gifts import _stars_amount, _unique_meta


def _unique(**extra: object) -> SimpleNamespace:
    base = {
        "slug": "SpyAgaric-1",
        "num": 1,
        "title": "Spy Agaric",
        "gift_id": 7,
        "owner_id": SimpleNamespace(user_id=777000),
        "owner_address": None,
        "gift_address": None,
        "burned": False,
        "crafted": None,
        "resell_amount": None,
        "availability_issued": 10,
        "availability_total": 1000,
    }
    base.update(extra)
    return SimpleNamespace(**base)


def test_stars_fallback_when_currency_missing() -> None:
    meta = _unique_meta(_unique(stars=98500))
    assert meta["value_amount"] == 98500
    assert meta["value_currency"] == "STARS"


def test_value_amount_wins_over_stars() -> None:
    meta = _unique_meta(_unique(value_amount=3, value_currency="TON", stars=98500))
    assert meta["value_amount"] == 3
    assert meta["value_currency"] == "TON"


def test_resell_amount_is_summed() -> None:
    meta = _unique_meta(
        _unique(
            value_amount=2,
            value_currency="TON",
            resell_amount=[SimpleNamespace(amount=1000), SimpleNamespace(amount=500)],
        )
    )
    assert meta["resell_amount"] == 1500
    assert _stars_amount(None) is None


def test_missing_currency_is_not_guessed_and_ton_is_not_stars() -> None:
    from telethon.tl.types import StarsAmount, StarsTonAmount

    meta = _unique_meta(_unique(value_amount=98500))
    assert meta["value_currency"] is None
    assert _stars_amount([StarsAmount(amount=4, nanos=0), StarsTonAmount(amount=2_000_000_000)]) == 4
    assert _stars_amount([StarsTonAmount(amount=2_000_000_000)]) is None
