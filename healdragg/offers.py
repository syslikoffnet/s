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
    allowed = {"max_nft", "location", "gender", "country", "backdrop", "min_value_usd_cents", "query", "after", "limit"}
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
    for key in ("backdrop", "query", "after"):
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
