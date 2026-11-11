from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from telethon import functions, types

from healdragg import offer_delivery
from healdragg.offer_delivery import Ledger
from healdragg.tg import Account, RateLimiter


def body(**changes):
    plan = {'currency': 'star', 'usd_per_unit': '0.02', 'markup_percent': '0',
            'max_per_offer': 600, 'total_budget': 1100, 'max_offers': 2}
    plan.update(changes)
    return {'session': 'writer', 'plan': plan, 'language': 'ru'}


@pytest.fixture
def scenario(store, tmp_path):
    store.add_session('writer', 'writer.session', purpose='offers')
    for i in (1, 2):
        store.upsert_nft_item(f'Bear-{i}', owner_tg_id=i, owner_username=f'seller{i}', value_usd=1000)
    ledger = Ledger(tmp_path / 'ledger.db')
    yield store, ledger
    ledger.close()


def test_prepared_is_immutable_and_language_correct(scenario):
    store, ledger = scenario
    prepared = ledger.prepare(store, {**body(), 'language': 'ja'})
    assert prepared['status'] == 'prepared' and prepared['total'] == 1000
    assert prepared['rows'][0]['command'] == '/createoffer @seller1 https://t.me/nft/Bear-1 500 star ja'
    store.upsert_nft_item('Bear-1', owner_tg_id=1, owner_username='othername', value_usd=5000)
    assert ledger.detail(prepared['id'])['rows'][0]['command'] == prepared['rows'][0]['command']
    assert ledger.start(prepared['id'])['status'] == 'running'
    with pytest.raises(ValueError):
        ledger.start(prepared['id'])


@pytest.mark.parametrize('value', ['evil', '', None, 1])
def test_invalid_language_rejected(scenario, value):
    store, ledger = scenario
    with pytest.raises(ValueError):
        ledger.prepare(store, {**body(), 'language': value})


def test_budget_dedup_and_uncertainty_across_restarts(scenario, tmp_path):
    store, ledger = scenario
    run = ledger.prepare(store, body())
    ledger.start(run['id'])
    aid, _ = ledger.reserve(run, run['rows'][0], 777)
    ledger.outcome(run['id'], 0, aid, 'unknown', 'lost')
    ledger.finish(run['id'], 'error')
    assert ledger.unresolved() == 1
    with pytest.raises(ValueError, match='сверк'):
        ledger.prepare(store, body())
    ledger.close()
    ledger.__init__(tmp_path / 'ledger.db')
    assert ledger.unresolved() == 1
    ledger.acknowledge()
    ledger.conn.execute("UPDATE attempts SET created_at=created_at-60")
    new = ledger.prepare(store, body())
    ledger.start(new['id'])
    with pytest.raises(ValueError, match='повтор запрещён'):
        ledger.reserve(new, new['rows'][0], 777)


def test_recovery_and_restore_are_fail_closed(scenario):
    store, ledger = scenario
    run = ledger.prepare(store, body())
    ledger.start(run['id'])
    ledger.reserve(run, run['rows'][0], 777)
    ledger.close()
    ledger.__init__(store.path.parent / 'ledger.db')
    assert ledger.detail(run['id'])['status'] == 'interrupted'
    assert ledger.history()[0]['status'] == 'unknown'
    ledger.block_restore()
    assert ledger.blocked()
    with pytest.raises(ValueError):
        ledger.prepare(store, body())
    ledger.acknowledge()
    assert not ledger.blocked() and not ledger.unresolved()


def test_hard_limits_applied_before_reserving(scenario):
    store, ledger = scenario
    with pytest.raises(ValueError, match='Ограничение безопасности'):
        ledger.prepare(store, body(usd_per_unit='0.0001', max_per_offer=100000, total_budget=100000))
    run = ledger.prepare(store, body())
    ledger.start(run['id'])
    aid, _ = ledger.reserve(run, run['rows'][0], 777)
    ledger.outcome(run['id'], 0, aid, 'telegram_ack', 'ok', 7)
    ledger.conn.execute("UPDATE attempts SET amount=9900,created_at=created_at-60 WHERE id=?", (aid,))
    with pytest.raises(ValueError, match='лимит'):
        ledger.reserve(run, run['rows'][1], 777)


async def test_single_send_targets_input_peer_and_exact_command():
    class Sender:
        def send(self, request):
            self.request = request
            f = asyncio.get_running_loop().create_future()
            f.set_result('ok')
            return f
    sender = Sender()
    peer = types.InputPeerUser(42, 99)
    command = '/createoffer @seller42 https://t.me/nft/Bear-1 100 star ru'
    result = await offer_delivery._send_once(SimpleNamespace(_sender=sender), peer, command, 123)
    assert result == 'ok' and isinstance(sender.request, functions.messages.SendMessageRequest)
    assert sender.request.peer.user_id == 42 and sender.request.message == command
    assert sender.request.random_id == 123 and sender.request.no_webpage


async def test_worker_ack_and_no_offers_claim(scenario, monkeypatch):
    store, ledger = scenario
    run = ledger.prepare(store, body(max_offers=1))
    ledger.start(run['id'])
    calls = []

    class Client:
        async def disconnect(self):
            calls.append('disconnect')

    async def open_account(*args):
        return Account('writer', Client(), limiter=RateLimiter(0, 0))

    async def verify(store, account, item):
        return {'account_id': 777}, types.InputPeerUser(item['seller_id'], 55)

    async def send(client, peer, command, random_id):
        calls.append((peer.user_id, command, random_id))
        return types.UpdateShortSentMessage(id=20, pts=1, pts_count=1, date=None, out=True)

    monkeypatch.setattr(offer_delivery, '_open_account', open_account)
    monkeypatch.setattr(offer_delivery, '_verify', verify)
    monkeypatch.setattr(offer_delivery, '_send_once', send)
    await offer_delivery.run(ledger, store, None, None, run['id'], asyncio.Lock())
    assert calls[0][:2] == (1, run['rows'][0]['command']) and calls[1] == 'disconnect'
    assert ledger.detail(run['id'])['status'] == 'done'
    result = ledger.history()[0]
    assert result['status'] == 'telegram_ack' and result['message_id'] == 20
    assert 'не подтверждены' in result['detail']


async def test_timeout_and_cancel_are_unknown_and_stop(scenario, monkeypatch):
    store, ledger = scenario
    run = ledger.prepare(store, body(max_offers=1))
    ledger.start(run['id'])
    started = asyncio.Event()
    calls = []

    class Client:
        async def disconnect(self):
            calls.append('disconnect')

    async def open_account(*args):
        return Account('writer', Client(), limiter=RateLimiter(0, 0))

    async def verify(store, account, item):
        return {'account_id': 777}, types.InputPeerUser(item['seller_id'], 55)

    async def send(*args):
        calls.append('send')
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(offer_delivery, '_open_account', open_account)
    monkeypatch.setattr(offer_delivery, '_verify', verify)
    monkeypatch.setattr(offer_delivery, '_send_once', send)
    task = asyncio.create_task(offer_delivery.run(ledger, store, None, None, run['id'], asyncio.Lock()))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == ['send', 'disconnect']
    assert ledger.history()[0]['status'] == 'unknown' and ledger.unresolved() == 1
    assert ledger.detail(run['id'])['status'] == 'stopped'


@pytest.mark.parametrize('reason', ['username', 'id', 'paid', 'blocked', 'bot', 'owner'])
async def test_live_verification_prevents_unsafe_chat(scenario, monkeypatch, reason):
    store, ledger = scenario
    item = ledger.prepare(store, body(max_offers=1))['rows'][0]
    user = types.User(id=1, username='seller1', access_hash=55)
    peer = types.PeerUser(1)
    full = SimpleNamespace(id=1, blocked=False, send_paid_messages_stars=None)
    check_result = {'account_id': 777, 'owner_matches': True, 'username_matches': True, 'premium': True,
                    'seller_username': 'seller1', 'bot': {'status': 'connected', 'missing_rights': [], 'all_chats': True}}
    if reason == 'username':
        user.username = 'wrongname'
    elif reason == 'id':
        peer = types.PeerUser(2)
    elif reason == 'paid':
        full.send_paid_messages_stars = 5
    elif reason == 'blocked':
        full.blocked = True
    elif reason == 'bot':
        check_result['bot']['status'] = 'other_connected'
    else:
        check_result['owner_matches'] = False

    class Client:
        async def __call__(self, request):
            if isinstance(request, functions.contacts.ResolveUsernameRequest):
                return SimpleNamespace(users=[user], peer=peer)
            if isinstance(request, functions.users.GetFullUserRequest):
                return SimpleNamespace(full_user=full)
            raise AssertionError(type(request))

    async def check(*args):
        return check_result

    monkeypatch.setattr(offer_delivery.offer_check, 'check', check)
    account = Account('writer', Client(), limiter=RateLimiter(0, 0))
    with pytest.raises(ValueError):
        await offer_delivery._verify(store, account, item)
    assert not ledger.history()


async def test_live_verification_resolves_owner_as_private_peer(scenario, monkeypatch):
    store, ledger = scenario
    item = ledger.prepare(store, body(max_offers=1))['rows'][0]

    class Client:
        async def __call__(self, request):
            if isinstance(request, functions.contacts.ResolveUsernameRequest):
                return SimpleNamespace(users=[types.User(id=1, username='seller1', access_hash=77)], peer=types.PeerUser(1))
            if isinstance(request, functions.users.GetFullUserRequest):
                return SimpleNamespace(full_user=SimpleNamespace(id=1, blocked=False, send_paid_messages_stars=None))
            raise AssertionError(type(request))

    async def check(*args):
        return {'account_id': 777, 'owner_matches': True, 'username_matches': True, 'premium': True,
                'seller_username': 'seller1', 'bot': {'status': 'connected', 'missing_rights': [], 'all_chats': True}}

    monkeypatch.setattr(offer_delivery.offer_check, 'check', check)
    _, peer = await offer_delivery._verify(store, Account('writer', Client(), limiter=RateLimiter(0, 0)), item)
    assert isinstance(peer, types.InputPeerUser) and (peer.user_id, peer.access_hash) == (1, 77)


def test_interval_cannot_be_bypassed_by_new_run(scenario):
    store, ledger = scenario
    run = ledger.prepare(store, body(max_offers=1))
    ledger.start(run['id'])
    aid, _ = ledger.reserve(run, run['rows'][0], 777)
    ledger.outcome(run['id'], 0, aid, 'telegram_ack', 'ok', 9)
    ledger.finish(run['id'], 'done')
    run2 = ledger.prepare(store, body())
    ledger.start(run2['id'])
    with pytest.raises(ValueError, match='рано'):
        ledger.reserve(run2, run2['rows'][1], 777)
    assert ledger.seconds_until_next() > 0


async def test_lost_response_stops_batch_and_does_not_retry(scenario, monkeypatch):
    store, ledger = scenario
    run = ledger.prepare(store, body())
    ledger.start(run['id'])
    sent = []

    class Client:
        async def disconnect(self):
            pass

    async def open_account(*args):
        return Account('writer', Client(), limiter=RateLimiter(0, 0))

    async def verify(store, account, item):
        return {'account_id': 777}, types.InputPeerUser(item['seller_id'], 55)

    async def send(client, peer, command, random_id):
        sent.append(command)
        raise TimeoutError('response lost')

    monkeypatch.setattr(offer_delivery, '_open_account', open_account)
    monkeypatch.setattr(offer_delivery, '_verify', verify)
    monkeypatch.setattr(offer_delivery, '_send_once', send)
    await offer_delivery.run(ledger, store, None, None, run['id'], asyncio.Lock())
    assert sent == [run['rows'][0]['command']]
    result = ledger.detail(run['id'])
    assert result['status'] == 'error'
    assert [r['status'] for r in result['rows']] == ['unknown', 'pending']
    assert ledger.unresolved() == 1 and ledger.history()[0]['command'] == sent[0]
