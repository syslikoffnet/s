"""Перевод цен в валюту, выбранную в приложении.

Telegram отдаёт цену подарка в своей валюте (TON, звёзды) и не всегда так, как
удобно человеку. Здесь считаем «сколько это в рублях/долларах» по курсам,
которые пользователь может поправить в настройках.

Курсы — обычные числа, их можно менять без перезапуска движка: они лежат в
``meta`` базы.
"""

from __future__ import annotations

import json
from typing import Any

from .db import Store

#: Валюты, между которыми умеем переводить.
CURRENCIES: dict[str, str] = {
    "RUB": "₽",
    "USD": "$",
    "EUR": "€",
    "TON": "TON",
    "STARS": "★",
}

#: Разумные значения по умолчанию: 1 $ ≈ 92 ₽, 1 TON ≈ 5 $, 1 звезда ≈ 0.013 $.
DEFAULT_RATES: dict[str, float] = {
    "usd_rub": 92.0,
    "ton_usd": 5.0,
    "star_usd": 0.013,
}

KEY_CURRENCY = "display_currency"
KEY_RATES = "display_rates"


def _to_float(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number <= 0:
        return fallback
    return number


def read_display(store: Store) -> dict[str, Any]:
    """Текущая валюта и курсы из базы (с дефолтами)."""
    currency = (store.get_meta(KEY_CURRENCY) or "RUB").upper()
    if currency not in CURRENCIES:
        currency = "RUB"
    rates = dict(DEFAULT_RATES)
    raw = store.get_meta(KEY_RATES)
    if raw:
        try:
            stored = json.loads(raw)
        except (TypeError, ValueError):
            stored = {}
        if isinstance(stored, dict):
            for key, default in DEFAULT_RATES.items():
                rates[key] = round(_to_float(stored.get(key), default), 6)
    return {"currency": currency, "rates": rates, "currencies": list(CURRENCIES)}


def write_display(store: Store, payload: dict[str, Any]) -> dict[str, Any]:
    """Сохраняет валюту и курсы. Возвращает новое состояние."""
    currency = str(payload.get("currency") or "").upper().strip()
    if currency:
        if currency not in CURRENCIES:
            raise ValueError(f"неизвестная валюта: {currency}")
        store.set_meta(KEY_CURRENCY, currency)
    raw_rates = payload.get("rates")
    if isinstance(raw_rates, dict):
        current = read_display(store)["rates"]
        for key in DEFAULT_RATES:
            if key in raw_rates:
                current[key] = round(_to_float(raw_rates[key], current[key]), 6)
        store.set_meta(KEY_RATES, json.dumps(current))
    return read_display(store)


def _usd(amount: float, source: str | None, rates: dict[str, float]) -> float | None:
    """Приводим сумму к долларам, дальше переводим в любую валюту."""
    source = (source or "").upper()
    if source in {"USD", "USDT", "US$", "$"}:
        return amount
    if source in {"TON", "GRAM"}:
        return amount * rates["ton_usd"]
    if source in {"STARS", "STAR", "XTR", "⭐"}:
        return amount * rates["star_usd"]
    if source in {"RUB", "₽", "RUR"}:
        return amount / rates["usd_rub"]
    if source in {"EUR", "€"}:
        return amount * 1.08  # евро к доллару, грубо, но лучше чем ничего
    return None


def convert(amount: float, source: str | None, target: str, rates: dict[str, float]) -> float | None:
    """Переводит сумму из валюты Telegram в выбранную валюту приложения."""
    usd = _usd(amount, source, rates)
    if usd is None:
        return None
    target = (target or "RUB").upper()
    if target == "USD":
        return usd
    if target == "RUB":
        return usd * rates["usd_rub"]
    if target == "EUR":
        return usd / 1.08
    if target == "TON":
        return usd / rates["ton_usd"]
    if target == "STARS":
        return usd / rates["star_usd"]
    return usd * rates["usd_rub"]


def format_price(
    amount: float | int | None,
    source: str | None,
    *,
    currency: str,
    rates: dict[str, float],
) -> str | None:
    """Готовая строка цены: ``1 234 ₽`` / ``13,5 TON`` / ``985 ★``."""
    if not amount:
        return None
    value = convert(float(amount), source, currency, rates)
    if value is None:
        return None
    sign = CURRENCIES.get(currency, currency)
    if currency == "STARS":
        rounded = round(value)
        return f"{rounded:,}".replace(",", " ") + f" {sign}"
    if abs(value) >= 100:
        text = f"{round(value):,}".replace(",", " ")
    else:
        text = f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    if currency == "TON":
        return f"{text} TON"
    return f"{text} {sign}"


def enrich_row(
    row: dict[str, Any],
    *,
    currency: str,
    rates: dict[str, float],
    amount_key: str = "value_amount",
    currency_key: str = "value_currency",
) -> dict[str, Any]:
    """Дописывает в строку ``price_display`` — для карточек и CSV."""
    amount = row.get(amount_key)
    source = row.get(currency_key)
    # Данные уникального подарка Telegram: value_usd — центы, а не доллары.
    # Сырые локальные фиатные суммы идут в минорных единицах и могут быть в
    # другой валюте; не конвертируем их, если нет известного экспонента.
    if row.get("slug") or row.get("is_nft"):
        if row.get("value_usd"):
            amount, source = row["value_usd"] / 100, "USD"
        elif str(source or "").upper() in {"USD", "EUR", "RUB"} and amount:
            amount = amount / 100
        elif str(source or "").upper() not in {"STARS", "STAR", "XTR"}:
            amount = None
    label = format_price(amount, source, currency=currency, rates=rates)
    if label:
        row["price_display"] = label
    return row


__all__ = [
    "CURRENCIES",
    "DEFAULT_RATES",
    "convert",
    "enrich_row",
    "format_price",
    "read_display",
    "write_display",
]
