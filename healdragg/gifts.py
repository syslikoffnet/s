"""OSINT по подаркам Telegram (star gifts).

Три направления:

1. **Профиль** — читаем сохранённые подарки пользователя, пишем «кто → кому →
   что → когда → с каким текстом» и спускаемся по дарителям на глубину.
2. **NFT-инвентарь** — уникальные подарки (StarGiftUnique) попадают в отдельную
   таблицу: у каждой вещи ровно один владелец, поэтому инвентарь удобно
   фильтровать по тому, где лежит NFT — в кошельке TON (``on_chain``) или
   внутри аккаунта Telegram.
3. **Сеть коллекций** — проверяем известные коллекции и номера подарков.
   Каталог, владение и результаты поиска могут быть неполными.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from telethon import utils
from telethon.errors import RPCError
from telethon.tl.functions.payments import (
    GetResaleStarGiftsRequest,
    GetSavedStarGiftsRequest,
    GetStarGiftsRequest,
    GetUniqueStarGiftRequest,
)
from telethon.tl.types import Document, PeerUser, StarGiftUnique, User

from .config import Settings
from .db import Store
from .tg import RateLimiter, SessionPool

log = logging.getLogger("healdragg.gifts")

Reporter = Callable[[dict[str, Any]], Awaitable[None]]

MAX_DEPTH = 8
MAX_PROFILES = 50_000
MAX_PER_PROFILE = 5000
MAX_HEAVY_PER_PROFILE = 100_000
HEAVY_PROFILE_THRESHOLD = 1000


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _stars_amount(value: Any) -> int | None:
    """Цена бывает числом, а бывает списком StarsAmount — сводим к числу."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    total = None
    for item in value or []:
        # StarsTonAmount хранит нано-TON, его нельзя суммировать со звёздами.
        if type(item).__name__ == "StarsTonAmount":
            continue
        amount = getattr(item, "amount", None)
        if isinstance(amount, int):
            total = (total or 0) + amount
    return total


def _gift_title(gift: Any, gift_id: int | None) -> str:
    title = getattr(gift, "title", None)
    if title:
        return str(title)
    return f"Подарок #{gift_id}" if gift_id else "Подарок"


def _slug_base(slug: Any) -> str | None:
    """``PlushPepe-123`` → ``PlushPepe`` (по этой базе перебираем номера)."""
    if not isinstance(slug, str):
        return None
    base, sep, tail = slug.rpartition("-")
    if not sep or not tail.isdigit():
        return None
    return base.strip() or None


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


def _unique_meta(gift: Any) -> dict[str, Any]:
    """Разбирает StarGiftUnique, включая признак «на кошельке».

    ``gift_address`` / ``owner_address`` означают, что вещь выпущена в TON и
    лежит в кошельке (чейнутая). Если адресов нет — NFT живёт в аккаунте
    Telegram (не чейнута), её и показываем отдельным фильтром.
    """
    slug = getattr(gift, "slug", None)
    owner_address = getattr(gift, "owner_address", None)
    gift_address = getattr(gift, "gift_address", None)
    owner_id = getattr(gift, "owner_id", None)
    owner_peer_id = _sender_id(owner_id)
    owner_name = getattr(gift, "owner_name", None)
    if not owner_name and owner_peer_id:
        owner_name = f"id {owner_peer_id}"
    resell = _stars_amount(getattr(gift, "resell_amount", None))
    value_amount = _as_int(getattr(gift, "value_amount", None))
    value_currency = getattr(gift, "value_currency", None)
    backdrop = next((getattr(attr, "name", None) for attr in (getattr(gift, "attributes", None) or [])
                     if type(attr).__name__ == "StarGiftAttributeBackdrop"), None)
    if value_amount is None:
        value_amount = _as_int(getattr(gift, "stars", None))
        if value_amount is not None:
            value_currency = "STARS"
    return {
        "slug": str(slug) if slug else None,
        "backdrop_name": str(backdrop) if backdrop else None,
        "num": _as_int(getattr(gift, "num", None)),
        "title": str(getattr(gift, "title", "") or "") or None,
        "gift_id": _as_int(getattr(gift, "gift_id", None))
        or _as_int(getattr(gift, "id", None)),
        "owner_tg_id": owner_peer_id,
        "host_tg_id": _sender_id(getattr(gift, "host_id", None)),
        "owner_name": str(owner_name) if owner_name else None,
        "owner_address": str(owner_address) if owner_address else None,
        "gift_address": str(gift_address) if gift_address else None,
        "on_chain": bool(owner_address or gift_address),
        "burned": bool(getattr(gift, "burned", False)),
        "crafted": _as_int(getattr(gift, "crafted", None)),
        "value_amount": value_amount,
        "value_currency": value_currency,
        "value_usd": _as_int(getattr(gift, "value_usd_amount", None)),
        "resell_amount": resell,
        "availability_issued": _as_int(getattr(gift, "availability_issued", None)),
        "availability_total": _as_int(getattr(gift, "availability_total", None)),
    }


def _remember_meta(
    store: Store,
    meta: dict[str, Any],
    *,
    owner_tg_id: int | None,
    owner_username: str | None,
    owner_name: str | None,
    chain_run_id: int | None = None,
) -> bool:
    """Пишет уникальную вещь в инвентарь и заводит её коллекцию."""
    slug = meta.get("slug")
    if not slug:
        return False
    base = _slug_base(slug)
    if base:
        store.upsert_nft_collection(
            base,
            gift_id=meta.get("gift_id"),
            title=meta.get("title"),
            total=meta.get("availability_total"),
        )
    return store.upsert_nft_item(
        slug,
        gift_id=meta.get("gift_id"),
        num=meta.get("num"),
        title=meta.get("title"),
        owner_tg_id=meta.get("owner_tg_id") or (owner_tg_id if not meta.get("on_chain") and not meta.get("host_tg_id") else None),
        owner_username=(owner_username if owner_tg_id and (meta.get("owner_tg_id") == owner_tg_id or
                        (not meta.get("owner_tg_id") and not meta.get("on_chain") and not meta.get("host_tg_id"))) else None),
        owner_name=meta.get("owner_name") or (owner_name if not meta.get("on_chain") and not meta.get("host_tg_id") else None),
        ownership_observed=True,
        owner_address=meta.get("owner_address"),
        gift_address=meta.get("gift_address"),
        on_chain=meta.get("on_chain"),
        burned=meta.get("burned"),
        crafted=meta.get("crafted"),
        value_amount=meta.get("value_amount"),
        value_currency=meta.get("value_currency"),
        value_usd=meta.get("value_usd"),
        resell_amount=meta.get("resell_amount"),
        availability_issued=meta.get("availability_issued"),
        availability_total=meta.get("availability_total"),
        backdrop_name=meta.get("backdrop_name"),
        chain_run_id=chain_run_id,
    )


async def _saved_gifts(
    client: Any,
    limiter: RateLimiter,
    peer: Any,
    per_profile: int,
    *,
    allow_heavy: bool = False,
    on_page: Callable[[list[Any], dict[int, User], int, int | None], Awaitable[None]] | None = None,
    on_wait: Callable[[int, int | None], Awaitable[None]] | None = None,
) -> tuple[list[Any], dict[int, User], bool, int | None]:
    """Читает страницы. Отсутствие фиксируем только при полном стабильном обходе."""
    saved: list[Any] = []
    pending: list[Any] = []
    users: dict[int, User] = {}
    offset = ""
    offsets = {offset}
    total: int | None = None
    total_changed = False
    received = 0
    while received < per_profile:
        limit = min(100, per_profile - received)
        if on_wait is not None:
            await on_wait(received, total)
        page = await limiter.call(
            lambda offset=offset, limit=limit: client(
                GetSavedStarGiftsRequest(peer=peer, offset=offset, limit=limit)
            )
        )
        count = _as_int(getattr(page, "count", None))
        if count is not None:
            if total is not None and count != total:
                total_changed = True
            total = count
        if not allow_heavy and total is not None and total >= HEAVY_PROFILE_THRESHOLD:
            return [], {}, False, total
        gifts = list(getattr(page, "gifts", None) or [])
        for user in getattr(page, "users", None) or []:
            if isinstance(user, User):
                users[int(user.id)] = user
        received += len(gifts)
        if not allow_heavy and received >= HEAVY_PROFILE_THRESHOLD:
            return [], {}, False, total or received
        next_offset = getattr(page, "next_offset", None)
        if on_page is not None:
            pending.extend(gifts)
            # Пока размер неизвестен, не разбираем профиль, который может
            # оказаться тяжёлым. Если Telegram назвал безопасный размер,
            # показываем результаты сразу после каждой страницы.
            if allow_heavy or total is not None or not next_offset:
                if pending:
                    await on_page(pending, users, received, total)
                    pending = []
        else:
            saved.extend(gifts)
        if not next_offset:
            complete = not total_changed and (total is None or received >= total)
            return saved, users, complete, None
        if not gifts or next_offset in offsets:
            return saved, users, False, None
        offsets.add(next_offset)
        offset = next_offset
    return saved, users, False, None


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
    allow_heavy: bool = False,
    skip_known: bool = False,
    expected_owner: int | None = None,
    expected_session_user: int | None = None,
    run_id: int | None = None,
) -> dict[str, Any]:
    """Собирает подарки пользователя и (опционально) его дарителей."""
    query = (query or "").strip()
    if not query:
        raise ValueError("нужен @username, ссылка или ID пользователя")
    depth = max(1, min(MAX_DEPTH, int(depth)))
    unlimited_depth = depth == MAX_DEPTH
    max_profiles = max(1, min(MAX_PROFILES, int(max_profiles)))
    per_profile = max(10, min(MAX_HEAVY_PER_PROFILE if allow_heavy else MAX_PER_PROFILE, int(per_profile)))

    async def report_stage(phase: str, profiles_done: int = 0, new_gifts: int = 0) -> None:
        if reporter is not None:
            await reporter({"phase": phase, "done": profiles_done, "total": max_profiles,
                            "found": new_gifts, "profiles_done": profiles_done,
                            "new_gifts": new_gifts})

    await report_stage("подключаю аккаунт для сбора")
    account = await pool.first(sessions)
    if expected_session_user is not None and getattr(account, "user_id", None) != expected_session_user:
        raise ValueError("Выбранный аккаунт изменился или его ID недоступен. Выберите аккаунт заново.")
    client = account.client
    limiter = account.limiter or RateLimiter()

    run = store.gift_crawl(run_id) if run_id is not None else None
    if run_id is not None and run is None:
        raise ValueError("Задание обхода не найдено")
    seed_user: User | None = None
    if run is None or not run["initialized"]:
        # Числовая строка в Telethon может трактоваться как телефон, а не user ID.
        seed_identifier: Any = int(query) if query.isascii() and query.isdigit() else query
        await report_stage(f"ищу профиль {query} · ожидание Telegram")
        entity = await limiter.call(lambda: client.get_entity(seed_identifier))
        seed_user = entity if isinstance(entity, User) else None
        if seed_user is None:
            users = getattr(entity, "users", None) or []
            seed_user = next((u for u in users if isinstance(u, User)), None)
        if seed_user is None:
            raise ValueError("это не пользователь — подарки ищутся по людям")
        if expected_owner is not None and int(seed_user.id) != int(expected_owner):
            raise ValueError("Имя пользователя больше не принадлежит указанному владельцу")
        if run_id is not None:
            store.init_gift_crawl(run_id, int(seed_user.id), seed_user.username,
                                  getattr(seed_user, "access_hash", None), False)
    queue: deque[tuple[User, int]] = deque([(seed_user, 0)] if seed_user and run_id is None else [])
    seen: set[int] = {int(seed_user.id)} if seed_user else set()
    results: list[dict[str, Any]] = []
    scanned = gifts_found = new_count = nft_seen = nft_new = errors = 0
    nft_chain = nft_hosted = 0

    while scanned < max_profiles:
        pending = store.gift_crawl_next(run_id) if run_id is not None else None
        if run_id is not None:
            if pending is None:
                break
            owner_id = int(pending["tg_id"])
            level = int(pending["level"])
            user = seed_user if seed_user is not None and int(seed_user.id) == owner_id else None
            if user is None and skip_known and store.gift_profile_known(owner_id):
                # Разворачиваем известные связи даже если профиль больше не разрешается.
                user = User(id=owner_id, username=pending.get("username"))
            if user is None:
                candidates: list[Any] = []
                if pending.get("username"):
                    candidates.append(pending["username"])
                candidates.append(owner_id)
                if pending.get("access_hash"):
                    from telethon.tl.types import InputPeerUser

                    candidates.append(InputPeerUser(owner_id, int(pending["access_hash"])))
                for candidate in candidates:
                    await report_stage(f"ищу профиль {owner_id} · ожидание Telegram", scanned, new_count)
                    try:
                        resolved = await limiter.call(lambda candidate=candidate: client.get_entity(candidate))
                        if isinstance(resolved, User) and int(resolved.id) == owner_id:
                            user = resolved
                            break
                    except Exception as exc:  # noqa: BLE001
                        log.debug("профиль %s недоступен: %s", owner_id, exc)
            if user is None:
                store.gift_crawl_finish_item(run_id, owner_id, "unavailable", "Профиль недоступен аккаунту")
                scanned += 1
                if reporter:
                    await reporter({"phase": "профиль недоступен", "done": scanned,
                                    "total": max_profiles, "found": new_count,
                                    "profiles_done": scanned, "new_gifts": new_count})
                continue
        else:
            if not queue:
                break
            user, level = queue.popleft()
            owner_id = int(user.id)
        username = user.username
        display = _user_label(user, owner_id)
        if skip_known and not allow_heavy and store.gift_profile_known(owner_id):
            # Уже прочитанный профиль не запрашиваем повторно, но продолжаем
            # обход по сохранённым дарителям, чтобы не обрывать сеть.
            if unlimited_depth or level + 1 < depth:
                for sender in store.gift_profile_senders(owner_id):
                    sender_id = int(sender["from_tg_id"])
                    if run_id is not None:
                        store.gift_crawl_enqueue(run_id, sender_id, level + 1, sender.get("username"))
                        continue
                    if sender_id in seen:
                        continue
                    seen.add(sender_id)
                    await report_stage(f"ищу дарителя {sender_id} · ожидание Telegram", scanned, new_count)
                    try:
                        entity = await limiter.call(
                            lambda sender=sender, sender_id=sender_id: client.get_entity(
                                sender.get("username") or sender_id
                            )
                        )
                        if isinstance(entity, User) and int(entity.id) == sender_id:
                            queue.append((entity, level + 1))
                    except Exception as exc:  # noqa: BLE001
                        log.debug("даритель %s недоступен: %s", sender_id, exc)
            deferred = store.gift_profile_deferred(owner_id)
            if run_id is not None:
                store.gift_crawl_finish_item(run_id, owner_id, "deferred" if deferred else "done")
            scanned += 1
            results.append({"owner_tg_id": owner_id, "owner_username": username,
                            "owner_name": display, "level": level, "skipped_known": True,
                            "deferred": deferred})
            if reporter is not None:
                await reporter({"phase": f"известный профиль · {display}", "done": scanned,
                                "total": min(max_profiles, scanned + len(queue)), "found": new_count,
                                "profiles_done": scanned, "new_gifts": new_count})
            continue
        profile_gifts = profile_nft = profile_new = 0
        error: str | None = None
        response = None
        complete = False
        heavy_count: int | None = None
        observed_ids: set[int] = set()
        snapshot_valid = True

        def process_item(
            item: Any, users: dict[int, User], *, owner_id: int = owner_id,
            username: str | None = username, display: str = display, level: int = level,
            observed_ids: set[int] = observed_ids,
        ) -> None:
            nonlocal snapshot_valid, profile_gifts, profile_nft, profile_new
            nonlocal gifts_found, new_count, nft_seen, nft_new, nft_chain, nft_hosted
            gift = getattr(item, "gift", None)
            if gift is None:
                snapshot_valid = False
                return
            gift_id = getattr(gift, "id", None)
            is_nft = isinstance(gift, StarGiftUnique)
            meta = _unique_meta(gift) if is_nft else {}
            title = _gift_title(gift, gift_id)
            sticker = getattr(gift, "sticker", None)
            if not is_nft and isinstance(gift_id, int) and isinstance(sticker, Document):
                store.remember_gift_sticker(gift_id, bytes(sticker))
            stars = getattr(gift, "stars", None)
            if stars is None and meta.get("value_currency") == "STARS":
                stars = meta.get("value_amount")
            sender_peer = getattr(item, "from_id", None)
            name_hidden = bool(getattr(item, "name_hidden", False))
            sender_id = None if name_hidden else _sender_id(sender_peer)
            sender = users.get(sender_id) if sender_id else None
            message = getattr(item, "message", None)
            text = getattr(message, "text", None) if message is not None else None
            saved_id = getattr(item, "saved_id", None) or getattr(item, "msg_id", None)
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
                date=_timestamp(getattr(item, "date", None)),
                is_nft=is_nft,
                name_hidden=name_hidden,
                slug=meta.get("slug"),
                num=meta.get("num"),
                value_amount=meta.get("value_amount"),
                value_currency=meta.get("value_currency"),
                resell_amount=meta.get("resell_amount"),
                availability_issued=meta.get("availability_issued"),
                availability_total=meta.get("availability_total"),
                on_chain=bool(meta.get("on_chain")),
                gift_address=meta.get("gift_address"),
                owner_address=meta.get("owner_address"),
                value_usd=meta.get("value_usd"),
                burned=bool(meta.get("burned")),
                crafted=meta.get("crafted"),
                raw=json.dumps([
                    {"type": type(attr).__name__, "name": getattr(attr, "name", None),
                     "rarity": getattr(getattr(attr, "rarity", None), "permille", None)}
                    for attr in (getattr(gift, "attributes", None) or [])
                    if getattr(attr, "name", None)
                ], ensure_ascii=False) if is_nft else None,
            )
            observed_id = store.gift_observation_id(
                owner_id, int(saved_id) if saved_id else None,
                int(gift_id) if gift_id else None, slug=meta.get("slug"),
                date=_timestamp(getattr(item, "date", None)), sender=sender_id,
            )
            if observed_id in observed_ids:
                # Повтор страницы или недостаточно данных для идентификации:
                # снимок небезопасно использовать для пометки отсутствующих.
                snapshot_valid = False
            observed_ids.add(observed_id)
            profile_gifts += 1
            gifts_found += 1
            if created:
                new_count += 1
                profile_new += 1
            if is_nft:
                nft_seen += 1
                profile_nft += 1
                if meta.get("on_chain"):
                    nft_chain += 1
                else:
                    nft_hosted += 1
                actual_owner = meta.get("owner_tg_id")
                if not actual_owner and not meta.get("on_chain") and not meta.get("host_tg_id"):
                    actual_owner = owner_id
                if meta.get("slug") and actual_owner:
                    store.observe_nft_owner(meta["slug"], actual_owner,
                                            username if actual_owner == owner_id else None,
                                            display if actual_owner == owner_id else meta.get("owner_name"))
                # Размещение в профиле не доказывает владение NFT из кошелька.
                _remember_meta(
                    store,
                    meta,
                    owner_tg_id=owner_id,
                    owner_username=username,
                    owner_name=display,
                )
                if created:
                    nft_new += 1
                    target_row = store.get_target(owner_id)
                    store.add_alert(
                        "gift",
                        f"NFT-подарок: {title}",
                        body=(
                            f"{display} ← {_user_label(sender, sender_id)}"
                            + (f" · #{meta['num']}" if meta.get("num") else "")
                            + (
                                f" · {meta['value_amount']} {meta['value_currency']}"
                                if meta.get("value_amount") and meta.get("value_currency")
                                else ""
                            )
                            + (f" · {stars}★" if isinstance(stars, int) else "")
                            + (" · в кошельке" if meta.get("on_chain") else " · в Telegram")
                            + (f" · «{str(text)[:120]}»" if text else "")
                        ),
                        target_pk=target_row["id"] if target_row else None,
                        dedup=f"gift:{owner_id}:{saved_id or gift_id}",
                    )
            if (unlimited_depth or level + 1 < depth) and sender_id:
                if run_id is not None:
                    store.gift_crawl_enqueue(
                        run_id, sender_id, level + 1,
                        getattr(sender, "username", None), getattr(sender, "access_hash", None),
                    )
                elif sender_id not in seen and sender:
                    seen.add(sender_id)
                    queue.append((sender, level + 1))


        async def process_page(
            gifts: list[Any], users: dict[int, User], received: int, total: int | None,
            *, display: str = display, profiles_done: int = scanned,
        ) -> None:
            for item in gifts:
                process_item(item, users)
            if reporter is not None:
                await reporter({
                    "phase": f"читаю подарки · {display}",
                    "done": received,
                    "total": total or 0,
                    "found": received,
                    "profiles_done": profiles_done,
                    "new_gifts": new_count,
                    "page_received": received,
                    "page_total": total,
                })

        async def wait_for_page(received: int, total: int | None, *, display: str = display,
                                profiles_done: int = scanned) -> None:
            if reporter is not None:
                await reporter({"phase": f"запрашиваю подарки · {display} · ожидание Telegram",
                                "done": received, "total": total or 0, "found": received,
                                "profiles_done": profiles_done, "new_gifts": new_count,
                                "page_received": received, "page_total": total})

        try:
            peer = utils.get_input_peer(user)
            saved, users, complete, heavy_count = await _saved_gifts(
                client, limiter, peer, per_profile,
                allow_heavy=allow_heavy,
                on_page=process_page,
                on_wait=wait_for_page,
            )
            if heavy_count is not None:
                store.defer_gift_profile(owner_id, username, display, heavy_count)
            else:
                response = (saved, users, complete)
        except Exception as exc:  # noqa: BLE001 — скрытые подарки не должны ронять обход
            log.warning("подарки %s недоступны: %s", owner_id, exc)
            error = str(exc)[:300]
            errors += 1

        if response is not None:
            saved, users, complete = response
            if complete and snapshot_valid:
                store.finish_gift_snapshot(owner_id, observed_ids)
            store.touch_gifts(owner_id)

        if run_id is not None:
            if not error and response is not None and (not complete or not snapshot_valid):
                error = "Профиль прочитан не полностью или выдача содержит повторяющиеся записи"
            state_name = "unavailable" if error else ("deferred" if heavy_count is not None else "done")
            store.gift_crawl_finish_item(run_id, owner_id, state_name, error)

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
                "deferred": response is None and error is None,
                "gift_count": heavy_count if response is None and error is None else None,
                "complete": bool(response and complete and snapshot_valid),
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
                    "profiles_done": scanned,
                    "new_gifts": new_count,
                }
            )

    summary = store.gift_summary()
    return {
        "query": query,
        "seed": {
            "tg_user_id": int(seed_user.id) if seed_user else 0,
            "username": seed_user.username if seed_user else None,
            "name": _user_label(seed_user, int(seed_user.id)) if seed_user else query,
        },
        "depth": depth,
        "max_profiles": max_profiles,
        "per_profile": per_profile,
        "profiles": scanned,
        "gifts": gifts_found,
        "new": new_count,
        "nft": nft_seen,
        "nft_new": nft_new,
        "nft_chain": nft_chain,
        "nft_hosted": nft_hosted,
        "errors": errors,
        "results": results,
        "summary": summary,
        "graph": store.gift_graph(seed=int(seed_user.id)) if seed_user else {"nodes": [], "edges": []},
    }


async def refresh_gifts(
    pool: SessionPool,
    store: Store,
    settings: Settings,
    *,
    limit: int = 60,
    per_profile: int = 100,
    sessions: list[str] | None = None,
    reporter: Reporter | None = None,
) -> dict[str, Any]:
    """Обновляет только уже известные профили: новые подарки и свежие цены NFT.

    Полезно, когда база собрана: не нужно заново обходить всю сеть.
    """
    owners = store.gift_owners(limit)
    total = len(owners)
    if not total:
        return {
            "owners": 0,
            "refreshed": 0,
            "gifts": 0,
            "new": 0,
            "nft": 0,
            "nft_chain": 0,
            "nft_hosted": 0,
            "errors": 0,
            "summary": store.gift_summary(),
            "results": [],
        }
    refreshed = gifts = new = nft = nft_chain = nft_hosted = errors = 0
    results: list[dict[str, Any]] = []
    for index, owner in enumerate(owners, 1):
        owner_id = int(owner["owner_tg_id"])
        query = owner.get("owner_username") or str(owner_id)
        try:
            result = await crawl_gifts(
                pool,
                store,
                settings,
                query,
                depth=1,
                max_profiles=1,
                per_profile=per_profile,
                sessions=sessions,
            )
            gifts += int(result.get("gifts", 0))
            new += int(result.get("new", 0))
            nft += int(result.get("nft", 0))
            nft_chain += int(result.get("nft_chain", 0))
            nft_hosted += int(result.get("nft_hosted", 0))
            refreshed += 1
            results.append({"owner_tg_id": owner_id, "query": query, "new": result.get("new", 0)})
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log.info("refresh gifts %s: %s", owner_id, exc)
            results.append({"owner_tg_id": owner_id, "query": query, "error": str(exc)[:200]})
        if reporter is not None:
            await reporter(
                {
                    "phase": f"обновляю {index}/{total}",
                    "done": index,
                    "total": total,
                    "found": new,
                }
            )
    return {
        "owners": total,
        "refreshed": refreshed,
        "gifts": gifts,
        "new": new,
        "nft": nft,
        "nft_chain": nft_chain,
        "nft_hosted": nft_hosted,
        "errors": errors,
        "summary": store.gift_summary(),
        "results": results,
    }


async def crawl_nft_profiles(
    pool: SessionPool,
    store: Store,
    settings: Settings,
    *,
    limit: int = 200,
    depth: int = 2,
    per_profile: int = 100,
    sessions: list[str] | None = None,
    reporter: Reporter | None = None,
) -> dict[str, Any]:
    """Читает подарки владельцев NFT, которых нашли при обходе коллекций.

    Это второй шаг «собери почти всех»: у каждого владельца NFT есть дарители,
    у дарителей — свои подарки. Так база растёт по живой сети, без глобального
    поиска пользователей (его у Telegram нет).
    """
    if reporter is not None:
        await reporter({"phase": "подбираю владельцев из базы", "done": 0, "total": limit,
                        "found": 0, "owners_done": 0, "errors": 0})
    owners = store.nft_owners_fresh(limit)
    total = len(owners)
    profiles = gifts = new = nft = errors = owners_done = 0
    last_error: str | None = None
    results: list[dict[str, Any]] = []
    if reporter is not None:
        await reporter({"phase": "владельцы подобраны" if total else "новых владельцев для чтения нет",
                        "done": 0, "total": limit, "found": 0, "owners_done": 0,
                        "owners_total": total, "errors": 0})
    index = 0

    async def report_inner(progress: dict[str, Any]) -> None:
        if reporter is None:
            return
        current = {
            "phase": progress.get("phase", "читаю профиль"),
            "done": min(limit, profiles + int(progress.get("profiles_done", 0))),
            "total": limit,
            "found": new + int(progress.get("new_gifts", 0)),
            "owners_done": owners_done, "owners_total": total,
            "current_owner": index, "errors": errors,
        }
        if last_error:
            current["last_error"] = last_error
        if progress.get("page_received") is not None:
            current["page_received"] = progress["page_received"]
            current["page_total"] = progress.get("page_total")
        await reporter(current)

    for index, owner in enumerate(owners, 1):
        if profiles >= limit:
            break
        owner_id = int(owner["owner_tg_id"])
        query = owner.get("owner_username") or str(owner_id)

        await report_inner({"phase": f"готовлю владельца {query}"})
        try:
            result = await crawl_gifts(
                pool,
                store,
                settings,
                query,
                depth=depth,
                max_profiles=min(MAX_PROFILES, max(1, limit - profiles)),
                per_profile=per_profile,
                sessions=sessions,
                skip_known=True,
                reporter=report_inner,
            )
            profiles += int(result.get("profiles", 0))
            gifts += int(result.get("gifts", 0))
            new += int(result.get("new", 0))
            nft += int(result.get("nft", 0))
            errors += int(result.get("errors", 0))
            for row in result.get("results", []):
                if row.get("error"):
                    last_error = str(row["error"])[:200]
            results.append({"owner_tg_id": owner_id, "query": query, "new": result.get("new", 0)})
        except Exception as exc:  # noqa: BLE001
            errors += 1
            last_error = str(exc)[:200] or type(exc).__name__
            log.info("профиль владельца NFT %s: %s", owner_id, exc)
            results.append({"owner_tg_id": owner_id, "query": query, "error": last_error})
        owners_done += 1
        await report_inner({"phase": f"обработан владелец {index}/{total}"})
    return {
        "owners": owners_done,
        "owners_total": total,
        "profiles": profiles,
        "gifts": gifts,
        "new": new,
        "nft": nft,
        "errors": errors,
        "last_error": last_error,
        "summary": store.gift_summary(),
        "results": results,
    }


async def _discover_collections(
    client: Any,
    limiter: RateLimiter,
    store: Store,
    budget: int,
    reserve: Callable[[str], bool],
    *,
    run_id: int | None = None,
    reporter: Reporter | None = None,
) -> dict[str, Any]:
    """Каталог подарков и первые страницы ресейла; прогресс задачи пишется в БД."""
    run = store.nft_chain_run(run_id) if run_id is not None else None
    used = int(run.get("discovery_used") or 0) if run else 0
    start_used = used
    entries = json.loads(run["discovery_ids"]) if run and run.get("discovery_ids") else None
    index = int(run.get("discovery_index") or 0) if run else 0
    found = new_items = discovery_errors = 0
    retrying = bool(run and str(run.get("inflight") or "").startswith("discovery:"))

    async def report(phase: str) -> None:
        if reporter is not None:
            await reporter({"phase": phase, "done": used, "total": budget, "found": new_items})

    if entries is None:
        if (used >= budget and not retrying) or not reserve("discovery:catalog"):
            return {"requests": 0, "collections": 0, "errors": 0}
        used += 1
        await report("получаю каталог подарков")
        catalog = await limiter.call(lambda: client(GetStarGiftsRequest(hash=0)), attempts=1)
        entries = []
        for gift in getattr(catalog, "gifts", None) or []:
            if not getattr(gift, "upgrade_stars", None) and not getattr(gift, "availability_total", None):
                continue
            gift_id = _as_int(getattr(gift, "id", None))
            if gift_id:
                entries.append({"id": gift_id, "title": getattr(gift, "title", None),
                                "total": _as_int(getattr(gift, "availability_total", None))})
        if run_id is not None:
            store.save_nft_chain_catalog(run_id, entries)
        await report(f"каталог: {len(entries)} подарков для проверки")
        retrying = False
    while index < len(entries) and (used < budget or retrying):
        entry = entries[index]
        gift_id = int(entry["id"])
        if not reserve(f"discovery:resale:{gift_id}"):
            break
        used += 1
        await report(f"проверяю продажи коллекции {index + 1}/{len(entries)}")
        try:
            resale = await limiter.call(
                lambda gift_id=gift_id: client(GetResaleStarGiftsRequest(gift_id=gift_id, offset="", limit=1)),
                attempts=1,
            )
        except RPCError as exc:
            # Официальный отказ для конкретного gift_id; ошибка сети, лимит или
            # любая другая причина оставляют эту позицию для продолжения.
            if getattr(exc, "message", None) != "STARGIFT_INVALID" or getattr(exc, "code", None) != 400:
                raise
            index += 1
            discovery_errors += 1
            retrying = False
            if run_id is not None:
                store.advance_nft_chain_discovery(
                    run_id, index, f"Telegram не поддерживает продажи подарка {gift_id}; он пропущен.")
            await report(f"коллекция {index}/{len(entries)} недоступна для поиска продаж")
            continue
        retrying = False
        for item in getattr(resale, "gifts", None) or []:
            if not isinstance(item, StarGiftUnique):
                continue
            meta = _unique_meta(item)
            base = _slug_base(meta.get("slug"))
            if not base:
                continue
            if meta.get("gift_id") != gift_id:
                raise ValueError("Telegram вернул NFT другой коллекции; позиция сохранена")
            # Найденная в продаже вещь сама является результатом, а не только
            # подсказкой для будущего перебора номеров.
            owner = meta.get("owner_tg_id")
            user = next((u for u in getattr(resale, "users", []) or []
                         if isinstance(u, User) and u.id == owner), None)
            created = _remember_meta(store, meta, owner_tg_id=owner,
                                     owner_username=getattr(user, "username", None),
                                     owner_name=meta.get("owner_name"), chain_run_id=run_id)
            new_items += int(created)
            found += 1
            break
        index += 1
        if run_id is not None:
            store.advance_nft_chain_discovery(run_id, index)
        await report(f"проверены продажи {index}/{len(entries)} · найдено коллекций {found}")
    if run_id is not None:
        store.finish_nft_chain_discovery(run_id)
    return {"requests": used - start_used, "collections": found, "new": new_items,
            "catalog_checked": index, "catalog_total": len(entries), "errors": discovery_errors}


async def resolve_unique_gift(pool: SessionPool, store: Store, slug: str) -> dict[str, Any]:
    """Запрашивает один конкретный NFT; не запускает обход коллекций."""
    account = await pool.first()
    limiter = account.limiter or RateLimiter()
    response = await limiter.call(lambda: account.client(GetUniqueStarGiftRequest(slug=slug)), attempts=1)
    gift = getattr(response, "gift", response)
    if not isinstance(gift, StarGiftUnique):
        raise ValueError("Telegram вернул неожиданный формат NFT")
    meta = _unique_meta(gift)
    canonical = str(meta.get("slug") or "")
    if canonical.casefold() != slug.casefold():
        raise ValueError("Telegram вернул другой NFT")
    owner = meta.get("owner_tg_id")
    owner_data = store.get_target(int(owner)) if owner else None
    _remember_meta(store, meta, owner_tg_id=owner,
                   owner_username=owner_data.get("username") if owner_data else None,
                   owner_name=meta.get("owner_name"))
    row = store.query_one("SELECT * FROM nft_items WHERE slug = ?", (canonical,))
    if not row:
        raise ValueError("Не удалось сохранить найденный NFT")
    return row


async def crawl_nft_chain(
    pool: SessionPool,
    store: Store,
    settings: Settings,
    *,
    requests: int = 500,
    sessions: list[str] | None = None,
    reporter: Reporter | None = None,
    discover: bool = True,
    slug: str | None = None,
    total: int | None = None,
    run_id: int | None = None,
) -> dict[str, Any]:
    """Перебирает NFT; курсор продвигается только после обработки ответа."""
    budget = max(1, min(2_000_000_000, int(requests)))
    run = store.nft_chain_run(run_id) if run_id is not None else None
    if run_id is not None and run is None:
        raise ValueError("Задача поиска NFT не найдена")
    used = int(run["used"]) if run else 0
    start_used = used
    if slug:
        base = slug.strip()
        if base:
            store.upsert_nft_collection(base, total=max(1, int(total or 0)))

    session = run.get("session") if run else None
    if session and session not in store.session_names():
        raise RuntimeError(f"Аккаунт «{session}» недоступен; восстановите его для продолжения поиска")
    account = await pool.first([session] if session else sessions)
    if session:
        expected = run.get("session_tg_id")
        actual = account.user_id
        if not actual or (expected and int(actual) != int(expected)):
            raise RuntimeError("Не удалось подтвердить исходный аккаунт Telegram для поиска NFT")
        if not expected:
            store.bind_nft_chain_account(run_id, session, int(actual))
    client = account.client
    limiter = account.limiter or RateLimiter()

    def reserve(subject: str) -> bool:
        nonlocal used
        if used >= budget:
            return False
        if run_id is not None and not store.reserve_nft_chain_request(run_id, subject):
            return False
        used += 1
        return True

    discovery: dict[str, Any] = {"requests": 0, "collections": 0, "errors": 0}
    if discover and (run is None or not run["discovery_done"]):
        discovery = await _discover_collections(
            client, limiter, store, max(1, min(80, budget // 4 or 1)), reserve, run_id=run_id,
            reporter=reporter,
        )

    items = new_items = owners = on_chain = hosted = errors = 0
    collections_done = 0
    last_error: str | None = None
    while used < budget:
        pending = store.nft_chain_run(run_id) if run_id is not None else None
        inflight = str(pending.get("inflight") or "") if pending else ""
        if inflight and not inflight.startswith("discovery:"):
            base, sep, number = inflight.rpartition("-")
            collection = store.query_one("SELECT * FROM nft_collections WHERE slug_base = ?", (base,))
            if not sep or not number.isdigit() or not collection:
                raise RuntimeError("Не найдена позиция прерванного запроса NFT")
            saved_cursor = int(collection["cursor"])
            if saved_cursor < int(number):
                raise RuntimeError("Позиция коллекции оказалась раньше прерванного запроса NFT")
            if saved_cursor > int(number):
                # Другой поиск уже обработал этот номер в общей базе.
                with store.tx() as conn:
                    conn.execute("UPDATE nft_chain_runs SET inflight=NULL WHERE id=? AND inflight=?",
                                 (run_id, inflight))
                collection = store.next_nft_collection()
        else:
            collection = store.next_nft_collection()
        if collection is None:
            break
        base = str(collection["slug_base"])
        collection_total = int(collection["total"] or 0)
        num = max(1, int(collection["cursor"] or 1))
        if collection_total <= 0 or num > collection_total:
            store.mark_nft_collection_done(base)
            continue
        item_slug = f"{base}-{num}"
        if not reserve(item_slug):
            break
        if reporter is not None:
            await reporter({"phase": f"проверяю {item_slug}", "done": used, "total": budget,
                            "found": new_items + discovery.get("new", 0)})
        invalid = False
        try:
            response = await limiter.call(lambda item_slug=item_slug: client(GetUniqueStarGiftRequest(slug=item_slug)),
                                          attempts=1)
        except RPCError as exc:
            # Telegram явно подтверждает неверный slug. Все прочие ошибки
            # останавливают проход и оставляют курсор на месте.
            if getattr(exc, "message", None) != "STARGIFT_SLUG_INVALID" or getattr(exc, "code", None) != 400:
                raise
            invalid = True
            errors += 1
            last_error = "Telegram не нашёл NFT с этим номером"
        created = False
        owner_found = False
        chain = False
        if not invalid:
            gift = getattr(response, "gift", response)
            if not isinstance(gift, StarGiftUnique):
                raise ValueError("Telegram вернул неожиданный формат NFT; позиция сохранена")
            items += 1
            meta = _unique_meta(gift)
            if str(meta.get("slug") or "").casefold() != item_slug.casefold():
                raise ValueError("Telegram вернул другой NFT; позиция сохранена")
            owner_tg_id = meta.get("owner_tg_id")
            username = None
            if owner_tg_id:
                user = next((u for u in getattr(response, "users", []) or []
                             if isinstance(u, User) and u.id == owner_tg_id), None)
                existing = store.get_target(owner_tg_id)
                username = getattr(user, "username", None) or (existing.get("username") if existing else None)
            created = _remember_meta(store, meta, owner_tg_id=owner_tg_id, owner_username=username,
                                     owner_name=meta.get("owner_name"), chain_run_id=run_id)
            new_items += int(created)
            owner_found = bool(owner_tg_id)
            owners += int(owner_found)
            chain = bool(meta.get("on_chain"))
            on_chain += int(chain)
            hosted += int(not chain)
        if run_id is not None:
            store.finish_nft_chain_item(run_id, base, cursor=num + 1, owner=owner_found, on_chain=chain, error=last_error if invalid else None)
        else:
            store.advance_nft_collection(base, cursor=num + 1, scanned=1, owners=int(owner_found),
                                         on_chain=int(chain), error=last_error if invalid else None)
        if num >= collection_total:
            latest = store.query_one("SELECT total FROM nft_collections WHERE slug_base = ?", (base,))
            if latest and num >= int(latest["total"]):
                collections_done += 1
        if reporter is not None:
            await reporter({"phase": f"{base}: {num} из {collection_total}",
                            "done": used, "total": budget, "found": new_items + discovery.get("new", 0)})

    summary = store.gift_summary()
    return {"requests": used - start_used, "budget": budget, "discovery": discovery,
            "collections": collections_done, "collections_known": len(store.nft_collections(500)),
            "items": items, "new": new_items + discovery.get("new", 0), "owners": owners, "on_chain": on_chain,
            "hosted": hosted, "errors": errors, "last_error": last_error, "summary": summary,
            "inventory": store.nft_summary(), "progress": store.nft_collections(50)}


__all__ = ["crawl_gifts", "refresh_gifts", "crawl_nft_chain", "crawl_nft_profiles"]
