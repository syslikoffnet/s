"""Просмотр локальных кандидатов: никакие сообщения и предложения не отправляются.

Эти наблюдения не доказывают актуальное владение, полный инвентарь или личные
качества владельца. Перед будущей отправкой необходима отдельная онлайн-проверка.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .db import Store


def backdrop_from_raw(raw: str | None) -> str | None:
    try:
        data = json.loads(raw or "null")
        if not isinstance(data, list):
            return None
        for attr in data:
            if isinstance(attr, dict) and attr.get("type") == "StarGiftAttributeBackdrop":
                value = attr.get("name")
                return value if isinstance(value, str) and value else None
    except (ValueError, TypeError):
        pass
    return None


def _integer(value: Any, label: str, maximum: int) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{label}: требуется целое число от 0 до {maximum}")
    return value


def candidates(store: Store, filters: dict[str, Any]) -> dict[str, Any]:
    """Страница по slug, выбор по всем данным, не по первым N строкам."""
    allowed = {"max_nft", "location", "gender", "country", "backdrop", "min_value_usd_cents", "query", "slug_exact", "after", "limit"}
    if not isinstance(filters, dict) or filters.keys() - allowed:
        raise ValueError("Неизвестные параметры отбора")
    maximum = _integer(filters.get("max_nft"), "Число NFT", 1_000_000)
    min_value = _integer(filters.get("min_value_usd_cents"), "Минимальная оценка", 1_000_000_000_000)
    limit = _integer(filters.get("limit", 40), "Размер страницы", 100)
    if not limit:
        raise ValueError("Размер страницы должен быть больше нуля")
    location = filters.get("location", "any")
    gender = filters.get("gender", "any")
    country = filters.get("country", "any")
    if location not in ("any", "telegram", "wallet") or gender not in ("any", "female") or country not in ("any", "RU"):
        raise ValueError("Неизвестное значение фильтра")
    strings = {}
    for key in ("backdrop", "query", "slug_exact", "after"):
        value = filters.get(key, "")
        if not isinstance(value, str) or len(value) > 200:
            raise ValueError(f"Некорректное поле {key}")
        strings[key] = value.strip()

    # Адрес подарка/кошелька лишь косвенно указывает на чейн. Его отсутствие
    # не является доказательством Telegram-владения.
    where = ["n.owner_tg_id IS NOT NULL", "n.owner_tg_id > 0", "n.burned = 0", "n.slug > ?"]
    args: list[Any] = [strings["after"]]
    if maximum is not None:
        where.append("(SELECT count(*) FROM nft_items owned WHERE owned.owner_tg_id=n.owner_tg_id AND owned.burned=0) <= ?")
        args.append(maximum)
    if location != "any":
        where.append("n.on_chain = ?")
        args.append(1 if location == "wallet" else 0)
    if gender == "female":
        where.append("t.gender = 'female'")
    if country == "RU":
        where.append("t.country = 'RU'")
    if min_value is not None:
        where.append("n.value_usd > 0 AND n.value_usd >= ?")
        args.append(min_value)
    if strings["backdrop"]:
        where.append("instr(hd_casefold(coalesce(n.backdrop_name, hd_backdrop((SELECT g.raw FROM gifts g "
                     "WHERE g.owner_tg_id = n.owner_tg_id AND g.slug = n.slug AND g.presence = 'visible' "
                     "ORDER BY g.seen_at DESC, g.id DESC LIMIT 1)), '')), ?) > 0")
        args.append(strings["backdrop"].casefold())
    if strings["slug_exact"]:
        where.append("n.slug = ? COLLATE NOCASE")
        args.append(strings["slug_exact"])
    needle = strings["query"].lstrip("@").casefold()
    if needle:
        where.append("instr(hd_casefold(coalesce(n.slug,'') || ' ' || coalesce(n.title,'') || ' ' || "
                     "coalesce(n.owner_username,'') || ' ' || coalesce(n.owner_name,'') || ' ' || "
                     "cast(n.owner_tg_id as text)), ?) > 0")
        args.append(needle)
    args.append(limit + 1)
    rows = store.query(
        "SELECT n.slug, n.title, n.num, n.owner_tg_id, n.owner_username, n.owner_name, "
        "n.on_chain, n.owner_address, n.gift_address, n.seen_at, n.value_usd, "
        "coalesce(n.backdrop_name, hd_backdrop((SELECT g.raw FROM gifts g "
        "WHERE g.owner_tg_id = n.owner_tg_id AND g.slug = n.slug AND g.presence = 'visible' "
        "ORDER BY g.seen_at DESC, g.id DESC LIMIT 1))) AS backdrop_name, "
        "t.gender, t.gender_confidence, t.country, t.country_confidence, "
        "(SELECT count(*) FROM nft_items owned WHERE owned.owner_tg_id=n.owner_tg_id AND owned.burned=0) AS observed_nft_count "
        f"FROM nft_items n LEFT JOIN targets t ON t.tg_user_id=n.owner_tg_id WHERE {' AND '.join(where)} "
        "ORDER BY n.slug LIMIT ?", args,
    )
    more = len(rows) > limit
    rows = rows[:limit]
    return {"rows": rows, "next_after": rows[-1]["slug"] if more else None}


def _decimal_text(value: Any, label: str, *, minimum: str, maximum: str, decimals: int) -> Any:
    """Без float и тихих значений по умолчанию: курс всегда указывает пользователь."""
    import re
    from decimal import Decimal, InvalidOperation

    if not isinstance(value, str) or not re.fullmatch(rf"\d{{1,10}}(?:\.\d{{1,{decimals}}})?", value):
        raise ValueError(f"{label}: введите положительное число с точкой")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label}: некорректное число") from exc
    if not Decimal(minimum) <= number <= Decimal(maximum):
        raise ValueError(f"{label}: допустимо от {minimum} до {maximum}")
    return number


def preview_batch(store: Store, payload: dict[str, Any]) -> dict[str, Any]:
    """Локальный план, НЕ цена и НЕ отправка: нельзя выдавать наблюдения за актуальные.

    Берём не более 5000 подходящих записей, выбираем по slug. Финансовые пределы
    задаются вручную и применяются после округления вверх до целой единицы.
    """
    import re
    from decimal import ROUND_CEILING

    allowed = {"filters", "currency", "usd_per_unit", "markup_percent", "max_per_offer",
               "total_budget", "max_offers", "one_per_owner"}
    if not isinstance(payload, dict) or payload.keys() - allowed:
        raise ValueError("Неизвестные параметры плана")
    filters = payload.get("filters", {})
    if not isinstance(filters, dict) or "after" in filters or "limit" in filters:
        raise ValueError("Фильтры плана не должны содержать курсор или размер страницы")
    currency = payload.get("currency")
    if currency not in ("gram", "star"):
        raise ValueError("Выберите валюту gram или star")
    rate = _decimal_text(payload.get("usd_per_unit"), "USD за единицу", minimum="0.00000001",
                         maximum="1000000", decimals=8)
    markup = _decimal_text(payload.get("markup_percent"), "Наценка", minimum="0", maximum="1000", decimals=2)
    per_offer = _integer(payload.get("max_per_offer"), "Максимум за NFT", 1_000_000_000)
    budget = _integer(payload.get("total_budget"), "Общий бюджет", 1_000_000_000)
    maximum = _integer(payload.get("max_offers"), "Количество", 100)
    if not per_offer or not budget or not maximum:
        raise ValueError("Укажите положительные лимиты на NFT, бюджет и количество")
    one_per_owner = payload.get("one_per_owner", True)
    if type(one_per_owner) is not bool:
        raise ValueError("Некорректный параметр количества на владельца")

    rows: list[dict[str, Any]] = []
    skipped = {"no_price": 0, "no_username": 0, "invalid_slug": 0, "same_owner": 0,
               "over_per_offer": 0, "over_budget": 0}
    owners: set[int] = set()
    after = ""
    examined = total = 0
    more = False
    # Страницы читаются с теми же фильтрами до применения пределов. Новые записи
    # между запросами могут сдвинуть результат; это лишь снимок для просмотра.
    while examined < 5000 and len(rows) < maximum:
        page = candidates(store, {**filters, "after": after, "limit": min(100, 5000 - examined)})
        consumed = 0
        for row in page["rows"]:
            consumed += 1
            examined += 1
            slug, name = row["slug"], row.get("owner_username")
            if not re.fullmatch(r"[A-Za-z0-9_]{1,80}-[0-9]{1,20}", slug):
                skipped["invalid_slug"] += 1
                continue
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", name):
                skipped["no_username"] += 1
                continue
            if not row.get("value_usd") or int(row["value_usd"]) <= 0:
                skipped["no_price"] += 1
                continue
            owner = int(row["owner_tg_id"])
            if one_per_owner and owner in owners:
                skipped["same_owner"] += 1
                continue
            amount = int(((int(row["value_usd"]) / (rate * 100)) * (1 + markup / 100)).to_integral_value(rounding=ROUND_CEILING))
            if amount > per_offer:
                skipped["over_per_offer"] += 1
                continue
            if total + amount > budget:
                skipped["over_budget"] += 1
                continue
            owners.add(owner)
            total += amount
            rows.append({"slug": slug, "owner_tg_id": owner, "owner_username": name,
                         "title": row.get("title"), "value_usd_cents": int(row["value_usd"]),
                         "seen_at": row.get("seen_at"), "amount": amount, "currency": currency,
                         "command": f"/createoffer @{name} https://t.me/nft/{slug} {amount} {currency} ru"})
            if len(rows) >= maximum:
                break
        more = bool(page["next_after"]) or consumed < len(page["rows"])
        if len(rows) >= maximum or not page["next_after"]:
            break
        after = page["next_after"]
    return {"rows": rows, "total_amount": total, "currency": currency, "examined": examined,
            "skipped": skipped, "truncated": more,
            "notice": "Черновик по старым наблюдениям и вручную введённому курсу. Владелец, оценка и курс не проверены; ничего не отправлено."}
