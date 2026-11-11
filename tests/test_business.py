from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon import functions, types

from healdragg import business
from healdragg.tg import Account


def bot() -> types.User:
    return types.User(id=123, access_hash=456, username="echidbot", bot=True, bot_business=True)


def connection(bot_id: int, rights=None, recipients=None):
    return types.ConnectedBot(bot_id=bot_id, rights=rights or types.BusinessBotRights(),
                              recipients=recipients or types.BusinessBotRecipients())


class Client:
    def __init__(self, connections=()):
        self.bot = bot()
        self.connections = list(connections)
        self.requests = []
        self.get_entity = AsyncMock(return_value=self.bot)

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.account.GetConnectedBotsRequest):
            return types.account.ConnectedBots(connected_bots=self.connections, users=[self.bot])
        assert isinstance(request, functions.account.UpdateConnectedBotRequest)
        assert not request.deleted
        recipients = types.BusinessBotRecipients(existing_chats=True, new_chats=True, contacts=True, non_contacts=True)
        self.connections.append(connection(self.bot.id, request.rights, recipients))
        return SimpleNamespace()


@pytest.mark.asyncio
async def test_connect_only_after_check_and_grants_only_message_rights():
    client = Client()
    account = Account(name="writer", client=client)
    assert (await business.status(account))["status"] == "not_connected"
    result = await business.connect(account)
    assert result["status"] == "connected" and result["all_chats"] and not result["missing_rights"]
    request = next(r for r in client.requests if isinstance(r, functions.account.UpdateConnectedBotRequest))
    assert all(getattr(request.rights, name) for name in business.MESSAGE_RIGHTS)
    assert not request.rights.transfer_stars and not request.rights.sell_gifts and not request.rights.edit_username
    assert all(getattr(request.recipients, name) for name in ("existing_chats", "new_chats", "contacts", "non_contacts"))
    await business.connect(account)
    assert sum(isinstance(r, functions.account.UpdateConnectedBotRequest) for r in client.requests) == 1


@pytest.mark.asyncio
async def test_other_bot_is_never_replaced():
    client = Client([connection(789)])
    account = Account(name="writer", client=client)
    assert (await business.connect(account))["status"] == "other_connected"
    assert not any(isinstance(r, functions.account.UpdateConnectedBotRequest) for r in client.requests)


@pytest.mark.asyncio
async def test_existing_bot_with_limited_permissions_is_not_silently_changed():
    client = Client([connection(123, types.BusinessBotRights(reply=True))])
    result = await business.connect(Account(name="writer", client=client))
    assert result["status"] == "connected"
    assert "delete_received_messages" in result["missing_rights"]
    assert not result["all_chats"]
    assert not any(isinstance(r, functions.account.UpdateConnectedBotRequest) for r in client.requests)


@pytest.mark.asyncio
async def test_non_business_or_mismatched_username_refused():
    client = Client()
    client.get_entity.return_value = types.User(id=123, access_hash=1, username="other", bot=True, bot_business=True)
    with pytest.raises(ValueError):
        await business.connect(Account(name="writer", client=client))
    assert not client.requests
