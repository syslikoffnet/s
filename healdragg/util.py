"""Мелочи, которые не заслуживают отдельного модуля."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from datetime import tzinfo as TzBase
from typing import TypeVar
from zoneinfo import ZoneInfo

T = TypeVar("T")

#: Сокращения месяцев в родительном падеже — как в списках Telegram.
MONTHS_SHORT = (
    "янв.",
    "февр.",
    "мар.",
    "апр.",
    "мая",
    "июн.",
    "июл.",
    "авг.",
    "сент.",
    "окт.",
    "нояб.",
    "дек.",
)

MONTHS_FULL = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


#: Смещения на случай, когда база часовых поясов недоступна (Android без tzdata).
_TZ_HOURS: dict[str, float] = {
    "UTC": 0,
    "Etc/UTC": 0,
    "GMT": 0,
    "Europe/London": 0,
    "Europe/Lisbon": 0,
    "Europe/Berlin": 1,
    "Europe/Paris": 1,
    "Europe/Warsaw": 1,
    "Europe/Prague": 1,
    "Europe/Rome": 1,
    "Europe/Madrid": 1,
    "Europe/Amsterdam": 1,
    "Europe/Vienna": 1,
    "Europe/Zurich": 1,
    "Europe/Belgrade": 1,
    "Europe/Budapest": 1,
    "Europe/Kyiv": 2,
    "Europe/Kiev": 2,
    "Europe/Chisinau": 2,
    "Europe/Kaliningrad": 2,
    "Europe/Helsinki": 2,
    "Europe/Athens": 2,
    "Europe/Bucharest": 2,
    "Europe/Sofia": 2,
    "Europe/Riga": 2,
    "Europe/Vilnius": 2,
    "Europe/Tallinn": 2,
    "Europe/Moscow": 3,
    "Europe/Minsk": 3,
    "Europe/Istanbul": 3,
    "Europe/Simferopol": 3,
    "Europe/Volgograd": 3,
    "Asia/Tbilisi": 4,
    "Asia/Yerevan": 4,
    "Asia/Baku": 4,
    "Asia/Dubai": 4,
    "Asia/Samarkand": 5,
    "Asia/Tashkent": 5,
    "Asia/Almaty": 5,
    "Asia/Astana": 5,
    "Asia/Yekaterinburg": 5,
    "Asia/Omsk": 6,
    "Asia/Bishkek": 6,
    "Asia/Novosibirsk": 7,
    "Asia/Krasnoyarsk": 7,
    "Asia/Irkutsk": 8,
    "Asia/Yakutsk": 9,
    "Asia/Vladivostok": 10,
    "Asia/Magadan": 11,
    "Asia/Kamchatka": 12,
    "Pacific/Auckland": 12,
    "America/New_York": -5,
    "America/Chicago": -6,
    "America/Denver": -7,
    "America/Los_Angeles": -8,
}


def tzinfo(name: str) -> TzBase:
    """Часовой пояс с гарантией: без tzdata возвращаем фиксированное смещение.

    На Android ``zoneinfo`` есть, а базы зон нет — ``ZoneInfo`` бросает
    ``ZoneInfoNotFoundError``, и раньше это роняло все ответы API. Теперь
    приложение просто считает время по известному смещению.
    """
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — любые проблемы с базой зон
        pass
    hours = _TZ_HOURS.get(name)
    if hours is None:
        return timezone.utc
    return timezone(timedelta(hours=hours), name)


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def fmt_day(ts: int | float | None, tz: str = "Europe/Moscow") -> str:
    """``09 нояб.`` — день месяца с ведущим нулём и точкой."""
    if not ts:
        return "—"
    dt = datetime.fromtimestamp(float(ts), tz=tzinfo(tz))
    return f"{dt.day:02d} {MONTHS_SHORT[dt.month - 1]}"


def fmt_dt(ts: int | float | None, tz: str = "Europe/Moscow") -> str:
    if not ts:
        return "—"
    dt = datetime.fromtimestamp(float(ts), tz=tzinfo(tz))
    return f"{dt.day:02d} {MONTHS_SHORT[dt.month - 1]} {dt.year}, {dt:%H:%M}"


def fmt_relative(ts: int | float | None, tz: str = "Europe/Moscow") -> str:
    if not ts:
        return "никогда"
    delta = max(0, now_ts() - int(ts))
    if delta < 60:
        return "только что"
    if delta < 3600:
        return f"{delta // 60} мин назад"
    if delta < 86400:
        return f"{delta // 3600} ч назад"
    if delta < 86400 * 7:
        return f"{delta // 86400} дн назад"
    return fmt_day(ts, tz)


def plural(number: int, forms: tuple[str, str, str]) -> str:
    """1 цель / 2 цели / 5 целей."""
    n = abs(int(number)) % 100
    if 11 <= n <= 19:
        return forms[2]
    n %= 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]


def human_int(value: int | float | None) -> str:
    """Без разделителей — так считает Telegram. 2750, а не 2 750."""
    if value is None:
        return "—"
    return str(int(value))


def human_size(num_bytes: int | float) -> str:
    size = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} с"
    if seconds < 3600:
        return f"{seconds // 60} мин {seconds % 60:02d} с"
    return f"{seconds // 3600} ч {seconds % 3600 // 60:02d} мин"


def group_link(tg_id: int, username: str | None = None, kind: str = "group") -> str | None:
    """Ссылка на чат: публичная по username, иначе t.me/c/… для участников."""
    if username:
        return f"https://t.me/{username}"
    if kind not in {"channel", "supergroup"}:
        return None
    raw = int(tg_id)
    internal = -raw - 1000000000000 if raw < 0 else raw
    if internal <= 0:
        return None
    return f"https://t.me/c/{internal}/1"


def truncate(text: str, limit: int, suffix: str = "..") -> str:
    """Обрезка по «видимой» длине с учётом широких эмодзи."""
    if limit <= 0:
        return ""
    text = " ".join((text or "").split())
    if _width(text) <= limit:
        return text
    out: list[str] = []
    width = 0
    for ch in text:
        w = _char_width(ch)
        if width + w > limit:
            break
        out.append(ch)
        width += w
    return "".join(out).rstrip() + suffix


def _char_width(ch: str) -> int:
    if unicodedata.combining(ch) or ch in "\u200d\ufe0f":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1


def _width(text: str) -> int:
    return sum(_char_width(ch) for ch in text)


def chunked(items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


@dataclass(slots=True, frozen=True)
class Page:
    """Окно постраничной выдачи."""

    page: int
    pages: int
    total: int
    size: int

    @classmethod
    def build(cls, total: int, page: int, size: int) -> Page:
        size = max(1, size)
        pages = max(1, math.ceil(total / size)) if total else 1
        page = clamp(int(page or 1), 1, pages)
        return cls(page=page, pages=pages, total=max(0, total), size=size)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    def window(self, radius: int = 2) -> list[int]:
        """Номера страниц вокруг текущей: 1 … 4 5 6 … 12."""
        pages = self.pages
        if pages <= 7:
            return list(range(1, pages + 1))
        start = max(1, self.page - radius)
        end = min(pages, self.page + radius)
        nums = list(range(start, end + 1))
        if start > 1:
            nums = [1, *([-1] if start > 2 else []), *nums]
        if end < pages:
            nums = [*nums, *([-1] if end < pages - 1 else []), pages]
        return nums


def ellipsis_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit < 5:
        return text[:limit]
    half = (limit - 1) // 2
    return f"{text[:half]}…{text[-half:]}"


def batched_join(parts: Iterable[str], sep: str = " · ") -> str:
    return sep.join(p for p in parts if p)


def safe_json(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, default=str)
