"""Явное подключение бизнес-бота к выбранному аккаунту (без рассылки)."""
from __future__ import annotations

from typing import Any

from telethon import functions, types, utils

from .tg import Account

BOT_USERNAME = "echidbot"
MESSAGE_RIGHTS = ("reply", "read_messages", "delete_sent_messages", "delete_received_messages")


async def _bot(account: Account) -> Any:
    if account.limiter:
        await account.limiter.wait()
    entity = await account.client.get_entity("@" + BOT_USERNAME)
    if (not isinstance(entity, types.User) or not entity.bot or not entity.bot_business
            or (entity.username or "").lower() != BOT_USERNAME):
        raise ValueError("@echidbot не найден или не поддерживает автоматизацию чатов")
    return entity


async def _connections(account: Account) -> Any:
    if account.limiter:
        await account.limiter.wait()
    return await account.client(functions.account.GetConnectedBotsRequest())


def _describe(bot: Any, result: Any) -> dict[str, Any]:
    connections = list(result.connected_bots)
    other = [c for c in connections if c.bot_id != bot.id]
    current = next((c for c in connections if c.bot_id == bot.id), None)
    users = {int(u.id): u for u in result.users if isinstance(u, types.User)}
    missing = [name for name in MESSAGE_RIGHTS if current and not getattr(current.rights, name, False)]
    recipients = current.recipients if current else None
    all_chats = bool(recipients and all(getattr(recipients, n, False) for n in
                                       ("existing_chats", "new_chats", "contacts", "non_contacts"))
                     and not getattr(recipients, "exclude_users", None)
                     and not getattr(recipients, "exclude_selected", False))
    status = "other_connected" if other else "connected" if current else "not_connected"
    return {
        "status": status,
        "bot_id": int(bot.id),
        "bot_username": BOT_USERNAME,
        "missing_rights": missing,
        "all_chats": all_chats,
        "other_bots": [
            {"id": int(c.bot_id), "username": getattr(users.get(int(c.bot_id)), "username", None)}
            for c in other
        ],
    }


async def status(account: Account) -> dict[str, Any]:
    bot = await _bot(account)
    return _describe(bot, await _connections(account))


async def connect(account: Account) -> dict[str, Any]:
    """Только после согласия; перепроверяем и не заменяем существующего бота.

    Это не атомарный compare-and-set с изменениями на других устройствах.
    Если ответ на запрос потерян, его не повторяем: состояние проверяется заново.
    """
    bot = await _bot(account)
    before = _describe(bot, await _connections(account))
    if before["status"] != "not_connected":
        return before
    request = functions.account.UpdateConnectedBotRequest(
        bot=utils.get_input_user(bot),
        recipients=types.InputBusinessBotRecipients(
            existing_chats=True, new_chats=True, contacts=True, non_contacts=True,
        ),
        rights=types.BusinessBotRights(
            reply=True, read_messages=True, delete_sent_messages=True, delete_received_messages=True,
        ),
    )
    if account.limiter:
        await account.limiter.wait()
    await account.client(request)  # мутацию при неопределённом ответе не повторяем
    after = _describe(bot, await _connections(account))
    if after["status"] != "connected":
        raise RuntimeError("Telegram не подтвердил подключение. Проверьте состояние ещё раз")
    return after
