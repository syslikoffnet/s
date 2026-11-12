"""Обогащение карточки цели: фото, bio, пол и страна по имени.

Данные берутся только из Telegram (профиль пользователя) и офлайн-словарей;
фото кэшируется файлом рядом с базой, наружу отдаётся как base64 в API.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from telethon import functions
from telethon.tl.types import InputUser, User, UserProfilePhotoEmpty

from .config import Settings
from .db import Store
from .intel import guess
from .tg import RateLimiter, SessionPool

log = logging.getLogger("healdragg.profile")

MAX_PHOTO_BYTES = 2_500_000


def photo_path(settings: Settings, tg_user_id: int) -> Path:
    return settings.home / "photos" / f"{tg_user_id}.jpg"


def _input_user(target: dict[str, Any]) -> Any:
    access_hash = int(target.get("access_hash") or 0)
    if access_hash:
        return InputUser(int(target["tg_user_id"]), access_hash)
    return int(target["tg_user_id"])


def _as_user(entity: Any) -> User | None:
    if isinstance(entity, User):
        return entity
    users = getattr(entity, "users", None)
    if users:
        return next((u for u in users if isinstance(u, User)), None)
    return None


async def refresh_profile(
    pool: SessionPool,
    store: Store,
    settings: Settings,
    target: dict[str, Any],
    *,
    sessions: list[str] | None = None,
    download_photo: bool = True,
) -> dict[str, Any]:
    """Обновляет bio, фото и «паспорт» цели. Возвращает свежие поля."""
    account = await pool.first(sessions)
    client = account.client
    limiter = account.limiter or RateLimiter()

    me = _input_user(target)
    try:
        entity = await limiter.call(lambda: client.get_entity(me))
    except Exception:  # noqa: BLE001 — access_hash мог устареть
        entity = await limiter.call(lambda: client.get_entity(int(target["tg_user_id"])))
    user = _as_user(entity)
    full_user = None
    try:
        full = await limiter.call(lambda: client(functions.users.GetFullUserRequest(entity)))
        full_user = getattr(full, "full_user", None)
        if user is None:
            user = _as_user(full)
    except Exception as exc:  # noqa: BLE001 — bio может быть скрыт
        log.debug("full user недоступен: %s", exc)

    bio = getattr(full_user, "about", None) or target.get("bio")
    fields: dict[str, Any] = {"bio": bio}

    if user is not None:
        fields["username"] = user.username or target.get("username")
        fields["first_name"] = user.first_name or target.get("first_name")
        fields["last_name"] = user.last_name or target.get("last_name")

    if download_photo and user is not None and not isinstance(user.photo, UserProfilePhotoEmpty):
        try:
            raw = await client.download_profile_photo(user, file=bytes)
        except Exception as exc:  # noqa: BLE001
            log.debug("фото не скачалось: %s", exc)
            raw = None
        if raw and 0 < len(raw) <= MAX_PHOTO_BYTES:
            path = photo_path(settings, int(target["tg_user_id"]))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            fields["photo_path"] = str(path)
            fields["photo_updated_at"] = _now()

    first_name = (fields.get("first_name") or target.get("first_name") or "").strip()
    if first_name:
        intel = guess(first_name)
        fields.update(
            {
                "gender": intel.gender,
                "gender_confidence": intel.gender_confidence,
                "country": intel.country,
                "country_name": intel.country_name,
                "country_confidence": intel.country_confidence,
                "intel_source": intel.source,
                "intel_updated_at": _now(),
            }
        )

    store.update_target_profile(int(target["tg_user_id"]), **fields)
    fresh = store.get_target(int(target["tg_user_id"])) or target
    return {
        "target": _public_target(fresh),
        "bio": fresh.get("bio"),
        "photo": _photo_info(settings, fresh),
        "intel": _intel_payload(fresh),
    }


def _now() -> int:
    import time

    return int(time.time())


def _public_target(target: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in target.items() if k not in {"access_hash", "photo_path"}}


def _photo_info(settings: Settings, target: dict[str, Any]) -> dict[str, Any]:
    path = Path(target.get("photo_path") or "")
    return {
        "present": bool(target.get("photo_path")) and path.is_file(),
        "updated_at": int(target.get("photo_updated_at") or 0),
    }


def _intel_payload(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "gender": target.get("gender"),
        "gender_confidence": target.get("gender_confidence"),
        "country": target.get("country"),
        "country_name": target.get("country_name"),
        "country_confidence": target.get("country_confidence"),
        "source": target.get("intel_source"),
        "updated_at": target.get("intel_updated_at"),
        "gifts_count": target.get("gifts_count") or 0,
        "gifts_updated_at": target.get("gifts_updated_at"),
    }


def photo_base64(settings: Settings, target: dict[str, Any]) -> dict[str, Any] | None:
    raw_path = target.get("photo_path")
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_file():
        return None
    data = path.read_bytes()
    if not data or len(data) > MAX_PHOTO_BYTES:
        return None
    return {
        "b64": base64.b64encode(data).decode("ascii"),
        "mime": "image/jpeg",
        "updated_at": int(target.get("photo_updated_at") or 0),
    }


__all__ = [
    "MAX_PHOTO_BYTES",
    "photo_base64",
    "photo_path",
    "refresh_profile",
]
