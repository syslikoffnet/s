"""OSINT по подаркам Telegram (star gifts).

Берём профиль пользователя, читаем его сохранённые подарки, записываем
«кто → кому → что → когда → с каким текстом», а затем по желанию спускаемся
по дарителям на заданную глубину. NFT-подарки (StarGiftUnique) попадают в
алерты и отдельный список.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from telethon import utils
from telethon.tl.functions.payments import GetSavedStarGiftsRequest
from telethon.tl.types import PeerUser, StarGiftUnique, User

from .config import Settings
from .db import Store
from .tg import RateLimiter, SessionPool

log = logging.getLogger("healdragg.gifts")

Reporter = Callable[[dict[str, Any]], Awaitable[None]]


def _gift_title(gift: Any, gift_id: int | None) -> str:
    title = getattr(gift, "title", None)
    if title:
        return str(title)
    return f"Подарок #{gift_id}" if gift_id else "Подарок"


def _sender_id(peer: Any) -> int | None:
    if isinstance(peer, PeerUser):
        return int(peer.user_id)
    user_id = getattr(peer, "user_id", None)
    return int(user_id) if user_id else None


def _timestamp(value: Any) -> int | None:
    if isinstance(value, datetime):
        return int(value.timestamp())
    return None


def _user_label(user: User | None, fallback_id: int | None = None) -> str:
    if user is not None:
        name = " ".join(part for part in (user.first_name, user.last_name) if part).strip()
        if user.username:
            handle = f"@{user.username}"
            return f"{name} ({handle})" if name else handle
        if name:
            return name
    return f"id {fallback_id}" if fallback_id else "неизвестно"


async def crawl_gifts(
    pool: SessionPool,
    store: Store,
    settings: Settings,
    query: str,
    *,
    depth: int = 1,
    max_profiles: int = 5,
    per_profile: int = 100,
    sessions: list[str] | None = None,
    reporter: Reporter | None = None,
) -> dict[str, Any]:
    """Собирает подарки пользователя и (опционально) его дарителей."""
    query = (query or "").strip()
    if not query:
        raise ValueError("нужен @username, ссылка или ID пользователя")
    depth = max(1, min(3, int(depth)))
    max_profiles = max(1, min(50, int(max_profiles)))
    per_profile = max(10, min(100, int(per_profile)))

    account = await pool.first(sessions)
    client = account.client
    limiter = account.limiter or RateLimiter()

    entity = await limiter.call(lambda: client.get_entity(query))
    seed_user = entity if isinstance(entity, User) else None
    if seed_user is None:
        users = getattr(entity, "users", None) or []
        seed_user = next((u for u in users if isinstance(u, User)), None)
    if seed_user is None:
        raise ValueError("это не пользователь — подарки ищутся по людям")

    queue: deque[tuple[User, int]] = deque([(seed_user, 0)])
    seen: set[int] = {int(seed_user.id)}
    results: list[dict[str, Any]] = []
    scanned = gifts_found = new_count = nft_seen = nft_new = errors = 0

    while queue and scanned < max_profiles:
        user, level = queue.popleft()
        owner_id = int(user.id)
        username = user.username
        display = _user_label(user, owner_id)
        profile_gifts = profile_nft = profile_new = 0
        error: str | None = None
        try:
            peer = utils.get_input_peer(user)
            response = await limiter.call(
                lambda peer=peer: client(
                    GetSavedStarGiftsRequest(peer=peer, offset="", limit=per_profile)
                )
            )
        except Exception as exc:  # noqa: BLE001 — скрытые подарки не должны ронять обход
            log.warning("подарки %s недоступны: %s", owner_id, exc)
            error = str(exc)[:300]
            errors += 1
            response = None

        if response is not None:
            users = {int(u.id): u for u in response.users if isinstance(u, User)}
            for saved in response.gifts:
                gift = getattr(saved, "gift", None)
                if gift is None:
                    continue
                gift_id = getattr(gift, "id", None)
                is_nft = isinstance(gift, StarGiftUnique)
                title = _gift_title(gift, gift_id)
                stars = getattr(gift, "stars", None)
                if stars is None:
                    stars = getattr(gift, "value_amount", None)
                sender_peer = getattr(saved, "from_id", None)
                name_hidden = bool(getattr(saved, "name_hidden", False))
                sender_id = None if name_hidden else _sender_id(sender_peer)
                sender = users.get(sender_id) if sender_id else None
                message = getattr(saved, "message", None)
                text = getattr(message, "text", None) if message is not None else None
                saved_id = getattr(saved, "saved_id", None) or getattr(saved, "msg_id", None)
                created = store.add_gift(
                    owner_id,
                    saved_id=int(saved_id) if saved_id else None,
                    gift_id=int(gift_id) if gift_id else None,
                    title=title,
                    kind="unique" if is_nft else "regular",
                    stars=int(stars) if isinstance(stars, int) else None,
                    from_tg_id=sender_id,
                    from_username=getattr(sender, "username", None) if sender else None,
                    from_name=_user_label(sender, sender_id) if sender else None,
                    owner_username=username,
                    owner_name=display,
                    message=(str(text)[:1000] if text else None),
                    date=_timestamp(getattr(saved, "date", None)),
                    is_nft=is_nft,
                    name_hidden=name_hidden,
                )
                profile_gifts += 1
                gifts_found += 1
                if created:
                    new_count += 1
                    profile_new += 1
                if is_nft:
                    nft_seen += 1
                    profile_nft += 1
                    if created:
                        nft_new += 1
                        target_row = store.get_target(owner_id)
                        store.add_alert(
                            "gift",
                            f"NFT-подарок: {title}",
                            body=(
                                f"{display} ← {_user_label(sender, sender_id)}"
                                + (f" · {stars}★" if isinstance(stars, int) else "")
                                + (f" · «{str(text)[:120]}»" if text else "")
                            ),
                            target_pk=target_row["id"] if target_row else None,
                            dedup=f"gift:{owner_id}:{saved_id or gift_id}",
                        )
                if level + 1 < depth and sender_id and sender_id not in seen and sender:
                    seen.add(sender_id)
                    queue.append((sender, level + 1))

            store.touch_gifts(owner_id)

        results.append(
            {
                "owner_tg_id": owner_id,
                "owner_username": username,
                "owner_name": display,
                "gifts": profile_gifts,
                "new": profile_new,
                "nft": profile_nft,
                "level": level,
                "error": error,
            }
        )
        scanned += 1
        if reporter is not None:
            await reporter(
                {
                    "phase": f"профиль {scanned} из {min(max_profiles, scanned + len(queue))}",
                    "done": scanned,
                    "total": min(max_profiles, scanned + len(queue)),
                    "found": new_count,
                }
            )

    summary = store.gift_summary()
    return {
        "query": query,
        "seed": {
            "tg_user_id": int(seed_user.id),
            "username": seed_user.username,
            "name": _user_label(seed_user, int(seed_user.id)),
        },
        "depth": depth,
        "max_profiles": max_profiles,
        "profiles": scanned,
        "gifts": gifts_found,
        "new": new_count,
        "nft": nft_seen,
        "nft_new": nft_new,
        "errors": errors,
        "results": results,
        "summary": summary,
        "graph": store.gift_graph(seed=int(seed_user.id)),
    }


__all__ = ["crawl_gifts"]
