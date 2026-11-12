"""Read-only online comparison for a draft NFT offer; never sends a message."""
from __future__ import annotations

import re
from typing import Any

from telethon import functions, types

from . import business
from .db import Store
from .offers import _integer
from .tg import RateLimiter, SessionPool
from .util import now_ts


async def _profile_contains(client: Any, limiter: RateLimiter, peer: types.InputPeerUser, slug: str) -> str:
    """Проверяем свежий список владельца, исключая размещённые на профиле NFT.

    Неполный список, ошибка или повтор курсора НЕ доказывают отсутствие NFT.
    """
    offset, seen = "", set()
    for _ in range(20):
        response = await limiter.call(lambda current=offset: client(functions.payments.GetSavedStarGiftsRequest(
            peer=peer, offset=current, limit=100, exclude_hosted=True)), attempts=1)
        for item in getattr(response, "gifts", []) or []:
            gift = getattr(item, "gift", None)
            if not isinstance(gift, types.StarGiftUnique) or (gift.slug or "").casefold() != slug.casefold():
                continue
            owner = gift.owner_id
            if (getattr(item, "refunded", False) or gift.burned or gift.owner_address or gift.gift_address or gift.host_id
                    or (owner is not None and (not isinstance(owner, types.PeerUser) or owner.user_id != peer.user_id))):
                return "conflict"
            return "profile"
        next_offset = getattr(response, "next_offset", None)
        if not next_offset:
            return "not_visible"
        if next_offset == offset or next_offset in seen:
            return "incomplete"
        seen.add(next_offset)
        offset = next_offset
    return "incomplete"


async def check(store: Store, pool: SessionPool, body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or body.keys() != {"session", "slug", "seller_id"}:
        raise ValueError("Укажите аккаунт, NFT и ID владельца")
    session, slug = body["session"], body["slug"]
    if not isinstance(session, str) or not session or len(session) > 200:
        raise ValueError("Некорректный аккаунт")
    if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,80}-[0-9]{1,20}", slug):
        raise ValueError("Некорректная ссылка NFT")
    seller_id = _integer(body["seller_id"], "ID владельца", 9_000_000_000_000_000_000)
    if not seller_id:
        raise ValueError("Укажите ID владельца")
    session_row = store.get_session(session)
    if not session_row or session_row.get("purpose") != "offers" or session_row.get("status") != "active":
        raise ValueError("Выберите активный аккаунт предложений")
    saved = store.query_one("SELECT slug,owner_tg_id,owner_username FROM nft_items WHERE slug = ? COLLATE NOCASE", (slug,))
    if not saved or saved.get("owner_tg_id") != seller_id:
        raise ValueError("Сохранённый владелец NFT изменился или неизвестен — обновите подбор")
    username = saved.get("owner_username")
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        raise ValueError("В базе нет корректного username владельца")

    account = await pool.connect(session, allow_reserved=True)
    client = account.client
    limiter = account.limiter or RateLimiter()
    me = await limiter.call(lambda: client.get_me(), attempts=1)
    if not isinstance(me, types.User) or not me.id or me.bot:
        raise ValueError("Не удалось определить аккаунт Telegram")
    bot = await business.status(account)
    resolved = await limiter.call(lambda: client(functions.contacts.ResolveUsernameRequest(username=username)), attempts=1)
    users = getattr(resolved, "users", []) or []
    user = next((u for u in users if isinstance(u, types.User) and u.id == seller_id), None)
    peer_id = getattr(getattr(resolved, "peer", None), "user_id", None)
    username_matches = bool(user and peer_id == seller_id and not user.deleted and not user.bot and not user.min
                            and (user.username or "").casefold() == username.casefold())
    result = await limiter.call(lambda: client(functions.payments.GetUniqueStarGiftRequest(slug=slug)), attempts=1)
    gift = getattr(result, "gift", result)
    if not isinstance(gift, types.StarGiftUnique) or (gift.slug or "").casefold() != slug.casefold():
        raise ValueError("Telegram не вернул запрошенный NFT")
    owner = gift.owner_id
    owner_id = owner.user_id if isinstance(owner, types.PeerUser) else None
    # Не утверждаем владение в Telegram при наличии адреса или хоста, а также
    # при скрытом/непользовательском владельце. Ответ — только снимок на сейчас.
    owner_matches = bool(owner_id == seller_id and not gift.burned and not gift.owner_address
                         and not gift.gift_address and not gift.host_id)
    if owner_matches:
        owner_state = "confirmed"
        ownership_source = "gift_owner_id"
    elif gift.burned:
        owner_state, ownership_source = "burned", None
    elif gift.owner_address or gift.gift_address or gift.host_id:
        owner_state, ownership_source = "wallet_or_hosted", None
    elif owner is not None:
        owner_state, ownership_source = "different", None
    elif username_matches and user and user.access_hash:
        owner_state = await _profile_contains(client, limiter, types.InputPeerUser(user.id, user.access_hash), gift.slug)
        owner_matches = owner_state == "profile"
        ownership_source = "profile_excluding_hosted" if owner_matches else None
    else:
        owner_state, ownership_source = "hidden", None
    value = gift.value_usd_amount
    if type(value) is not int or value <= 0:
        value = None
    return {
        "slug": gift.slug, "seller_id": seller_id, "seller_username": username,
        "telegram_owner_id": owner_id, "owner_matches": owner_matches,
        "owner_state": owner_state, "ownership_source": ownership_source,
        "username_matches": username_matches, "value_usd_cents": value,
        "account_id": int(me.id), "premium": bool(me.premium),
        "bot": bot, "checked_at": now_ts(),
        "notice": "Снимок Telegram, не гарантия владения, рыночной цены, работы бота или создания оффера. Команда не отправлена.",
    }
