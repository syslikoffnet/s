"""Explicit, bounded delivery of user-approved commands to NFT owners.

This records message attempts, NOT offers or payments. Unknown outcomes are never
retried and require manual reconciliation. The ledger is deliberately separate
from replaceable app backups.
"""
from __future__ import annotations

import asyncio
import re
import secrets
import sqlite3
import threading
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import uuid4

from telethon import TelegramClient, functions, types

from . import offer_check, offers
from .config import Settings
from .db import Store
from .tg import Account, RateLimiter, SessionPool
from .util import now_ts

LANGUAGES = {"ru", "en", "zh", "ar", "ko", "ja", "tl", "th", "ms"}
DAILY_CAP = {"star": 10_000, "gram": 10}
DAILY_COUNT = 20
MIN_INTERVAL = 60
PREPARE_TTL = 300


def _locked(method: Any) -> Any:
    @wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), timeout=10, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, session TEXT NOT NULL, currency TEXT NOT NULL,
                language TEXT NOT NULL, budget INTEGER NOT NULL, status TEXT NOT NULL,
                error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS delivery_one_running ON runs(status) WHERE status='running';
            CREATE TABLE IF NOT EXISTS items (
                run_id TEXT NOT NULL, position INTEGER NOT NULL, slug TEXT NOT NULL,
                seller_id INTEGER NOT NULL, seller_username TEXT NOT NULL,
                amount INTEGER NOT NULL, command TEXT NOT NULL, status TEXT NOT NULL,
                error TEXT, PRIMARY KEY(run_id, position)
            );
            CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, position INTEGER NOT NULL,
                slug TEXT NOT NULL COLLATE NOCASE UNIQUE, seller_id INTEGER NOT NULL,
                buyer_id INTEGER NOT NULL, currency TEXT NOT NULL, amount INTEGER NOT NULL,
                command TEXT NOT NULL, random_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL, message_id INTEGER, detail TEXT,
                created_at INTEGER NOT NULL, resolved INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS delivery_attempts_day ON attempts(created_at, currency);
        ''')
        # A crash during a send is unknown, never pending or retried.
        self.conn.execute("UPDATE attempts SET status='unknown', detail='Движок остановился во время отправки' WHERE status='sending'")
        self.conn.execute("UPDATE items SET status='unknown', error='Исход неизвестен: проверьте чат' WHERE status='sending'")
        self.conn.execute("UPDATE runs SET status='interrupted', error='Запуск прерван; сам не возобновляется', updated_at=? WHERE status='running'", (now_ts(),))

    @_locked
    def close(self) -> None:
        self.conn.close()

    @_locked
    def blocked(self) -> bool:
        row = self.conn.execute("SELECT value FROM meta WHERE key='restore_blocked'").fetchone()
        return bool(row and row[0] == '1')

    @_locked
    def block_restore(self) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES('restore_blocked','1')")

    @_locked
    def acknowledge(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES('restore_blocked','0')")
            self.conn.execute("UPDATE attempts SET resolved=1 WHERE status='unknown'")
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    @_locked
    def unresolved(self) -> int:
        return self.conn.execute("SELECT count(*) FROM attempts WHERE status IN ('unknown','sending') AND resolved=0").fetchone()[0]

    @_locked
    def detail(self, run_id: str | None = None) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id=?" if run_id else
                                "SELECT * FROM runs ORDER BY (status='running') DESC, rowid DESC LIMIT 1", (run_id,) if run_id else ()).fetchone()
        if not row:
            return None
        result = dict(row)
        result["rows"] = [dict(r) for r in self.conn.execute("SELECT * FROM items WHERE run_id=? ORDER BY position", (row['id'],))]
        result["total"] = sum(row['amount'] for row in result["rows"])
        return result

    @_locked
    def history(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT id,run_id,slug,seller_id,buyer_id,currency,amount,command,status,message_id,detail,created_at,resolved FROM attempts ORDER BY rowid DESC LIMIT 100")]

    @_locked
    def prepare(self, store: Store, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict) or body.keys() != {"session", "plan", "language"}:
            raise ValueError("Укажите аккаунт, черновик и язык")
        if self.blocked() or self.unresolved():
            raise ValueError("Сначала проверьте историю в Telegram и подтвердите сверку")
        session, language = body['session'], body['language']
        if not isinstance(session, str) or not session or len(session) > 200 or not isinstance(language, str) or language not in LANGUAGES:
            raise ValueError("Некорректный аккаунт или язык")
        account = store.get_session(session)
        if not account or account.get('status') != 'active' or account.get('purpose') != 'offers':
            raise ValueError("Выберите активный аккаунт предложений")
        plan = body['plan']
        preview = offers.preview_batch(store, plan)
        currency = plan['currency']
        if not preview['rows']:
            raise ValueError("В черновике нет подходящих NFT")
        if len(preview['rows']) > DAILY_COUNT or preview['total_amount'] > DAILY_CAP[currency]:
            raise ValueError(f"Ограничение безопасности: до {DAILY_COUNT} сообщений и {DAILY_CAP[currency]} {currency} в сутки")
        run_id, stamp = uuid4().hex, now_ts()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)",
                              (run_id, session, currency, language, plan['total_budget'], 'prepared', None, stamp, stamp))
            for index, row in enumerate(preview['rows']):
                command = f"/createoffer @{row['owner_username']} https://t.me/nft/{row['slug']} {row['amount']} {currency} {language}"
                self.conn.execute("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?)",
                                  (run_id, index, row['slug'], row['owner_tg_id'], row['owner_username'], row['amount'], command, 'pending', None))
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        return self.detail(run_id)

    @_locked
    def prepare_single(self, store: Store, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict) or body.keys() != {"session", "slug", "seller_id", "amount", "currency", "language"}:
            raise ValueError("Укажите аккаунт, NFT, владельца, сумму, валюту и язык")
        if self.blocked() or self.unresolved():
            raise ValueError("Сначала проверьте историю в Telegram и подтвердите сверку")
        session, slug, currency, language = body['session'], body['slug'], body['currency'], body['language']
        if not isinstance(session, str) or not session or len(session) > 200 or not isinstance(language, str) or language not in LANGUAGES:
            raise ValueError("Некорректный аккаунт или язык")
        if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,80}-[0-9]{1,20}", slug):
            raise ValueError("Некорректная ссылка NFT")
        if not isinstance(currency, str) or currency not in DAILY_CAP:
            raise ValueError("Выберите валюту gram или star")
        amount = offers._integer(body['amount'], "Сумма", DAILY_CAP[currency])
        seller = offers._integer(body['seller_id'], "ID владельца", 9_000_000_000_000_000_000)
        if not amount or not seller:
            raise ValueError("Укажите положительную сумму и ID владельца")
        account = store.get_session(session)
        if not account or account.get('status') != 'active' or account.get('purpose') != 'offers':
            raise ValueError("Выберите активный аккаунт предложений")
        saved = store.query_one("SELECT slug,owner_tg_id,owner_username,burned FROM nft_items WHERE slug = ? COLLATE NOCASE", (slug,))
        if not saved or saved.get('burned') or saved.get('owner_tg_id') != seller:
            raise ValueError("Владелец в базе неизвестен или изменился — обновите NFT")
        name = saved.get('owner_username')
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", name):
            raise ValueError("В базе нет корректного username владельца")
        slug = saved['slug']
        command = f"/createoffer @{name} https://t.me/nft/{slug} {amount} {currency} {language}"
        run_id, stamp = uuid4().hex, now_ts()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)",
                              (run_id, session, currency, language, amount, 'prepared', None, stamp, stamp))
            self.conn.execute("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?)",
                              (run_id, 0, slug, seller, name, amount, command, 'pending', None))
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        return self.detail(run_id)

    @_locked
    def start(self, run_id: Any) -> dict[str, Any]:
        if not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id):
            raise ValueError("Некорректная партия")
        if self.blocked() or self.unresolved():
            raise ValueError("Сначала проверьте историю в Telegram и подтвердите сверку")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row or row['status'] != 'prepared' or row['created_at'] + PREPARE_TTL <= now_ts():
                raise ValueError("Подтверждение истекло или партия уже запущена; подготовьте новую")
            if self.conn.execute("SELECT 1 FROM runs WHERE status='running'").fetchone():
                raise ValueError("Другая отправка уже выполняется")
            self.conn.execute("UPDATE runs SET status='running', updated_at=? WHERE id=?", (now_ts(), run_id))
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        return self.detail(run_id)

    @_locked
    def seconds_until_next(self) -> int:
        last = self.conn.execute("SELECT max(created_at) FROM attempts").fetchone()[0]
        return max(0, int(last) + MIN_INTERVAL - now_ts()) if last is not None else 0

    @_locked
    def reserve(self, run: dict[str, Any], item: dict[str, Any], buyer_id: int) -> tuple[str, int]:
        attempt_id, random_id, now = uuid4().hex, secrets.randbits(63) or 1, now_ts()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            current = self.conn.execute("SELECT status FROM runs WHERE id=?", (run['id'],)).fetchone()
            if not current or current[0] != 'running' or self.blocked() or self.unresolved():
                raise ValueError("Отправка остановлена или требуется сверка истории")
            if self.seconds_until_next() > 0:
                raise ValueError("Слишком рано для следующей команды")
            daily = self.conn.execute("SELECT coalesce(sum(amount),0) FROM attempts WHERE currency=? AND created_at>=?", (run['currency'], (now // 86400) * 86400)).fetchone()
            all_count = self.conn.execute("SELECT count(*) FROM attempts WHERE created_at>=?", ((now // 86400) * 86400,)).fetchone()[0]
            used = self.conn.execute("SELECT coalesce(sum(amount),0) FROM attempts WHERE run_id=?", (run['id'],)).fetchone()[0]
            if daily[0] + item['amount'] > DAILY_CAP[run['currency']] or all_count >= DAILY_COUNT or used + item['amount'] > run['budget']:
                raise ValueError("Достигнут суточный или общий лимит отправки")
            self.conn.execute("INSERT INTO attempts(id,run_id,position,slug,seller_id,buyer_id,currency,amount,command,random_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                              (attempt_id, run['id'], item['position'], item['slug'], item['seller_id'], buyer_id, run['currency'], item['amount'], item['command'], random_id, 'sending', now))
            self.conn.execute("UPDATE items SET status='sending' WHERE run_id=? AND position=?", (run['id'], item['position']))
            self.conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            self.conn.execute("ROLLBACK")
            raise ValueError("Попытка по этому NFT уже есть в журнале — повтор запрещён") from exc
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        return attempt_id, random_id

    @_locked
    def outcome(self, run_id: str, position: int, attempt_id: str, status: str, detail: str, message_id: int | None = None) -> None:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute("UPDATE attempts SET status=?,detail=?,message_id=? WHERE id=?", (status, detail, message_id, attempt_id))
            self.conn.execute("UPDATE items SET status=?,error=? WHERE run_id=? AND position=?", (status, detail, run_id, position))
            self.conn.execute("UPDATE runs SET updated_at=? WHERE id=?", (now_ts(), run_id))
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    @_locked
    def finish(self, run_id: str, status: str, error: str | None = None) -> None:
        self.conn.execute("UPDATE runs SET status=?,error=?,updated_at=? WHERE id=? AND status='running'", (status, error, now_ts(), run_id))


def message_id(result: Any, random_id: int) -> int | None:
    if isinstance(result, types.UpdateShortSentMessage) and result.out and result.id > 0:
        return result.id
    for update in getattr(result, 'updates', []) or []:
        if isinstance(update, types.UpdateMessageID) and update.random_id == random_id and update.id > 0:
            return update.id
    return None


async def _send_once(client: TelegramClient, peer: types.InputPeerUser, command: str, random_id: int) -> Any:
    request = functions.messages.SendMessageRequest(peer=peer, message=command, no_webpage=True, random_id=random_id)
    # The dedicated client has auto_reconnect=False, so pending requests are
    # not requeued after a lost connection. Do not call TelegramClient._call.
    return await asyncio.wait_for(client._sender.send(request), timeout=30)


async def _open_account(settings: Settings, store: Store, pool: SessionPool, session: str) -> Account:
    row = store.get_session(session)
    if not row or row['purpose'] != 'offers' or row['status'] != 'active':
        raise ValueError("Аккаунт предложений недоступен")
    await pool.disconnect(session)
    api_id, api_hash = settings.require_api()
    path = settings.sessions_dir / Path(row.get('session_file') or f'{session}.session').name
    if path.is_symlink() or not path.is_file():
        raise ValueError("Файл сессии отсутствует")
    client = TelegramClient(str(path), api_id, api_hash, connection_retries=0,
                            request_retries=0, auto_reconnect=False, flood_sleep_threshold=0,
                            device_model="Healdragg", system_version="Android 14", app_version="1.10.1")
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise ValueError("Сессия Telegram не авторизована")
        return Account(session, client, limiter=RateLimiter(settings.scan_min_delay, settings.scan_jitter))
    except BaseException:
        await client.disconnect()
        raise


async def _verify(store: Store, account: Account, item: dict[str, Any]) -> tuple[dict[str, Any], types.InputPeerUser]:
    class SinglePool:
        async def connect(self, name: str, *, allow_reserved: bool = False) -> Account:
            if name != account.name or not allow_reserved:
                raise ValueError("Неверная сессия")
            return account

    result = await offer_check.check(store, SinglePool(), {'session': account.name, 'slug': item['slug'], 'seller_id': item['seller_id']})
    bot = result['bot']
    if (not result['owner_matches'] or not result['username_matches'] or not result['premium']
            or result['seller_username'].casefold() != item['seller_username'].casefold()
            or result['account_id'] == item['seller_id'] or bot.get('status') != 'connected'
            or bot.get('missing_rights') or not bot.get('all_chats')):
        raise ValueError("Владелец, username, Premium или бизнес-бот не подтверждены")
    assert account.limiter is not None
    resolved = await account.limiter.call(lambda: account.client(functions.contacts.ResolveUsernameRequest(username=item['seller_username'])), attempts=1)
    user = next((u for u in getattr(resolved, 'users', []) if isinstance(u, types.User) and u.id == item['seller_id']), None)
    if (not user or user.deleted or user.bot or user.min or not user.access_hash
            or (user.username or '').casefold() != item['seller_username'].casefold()
            or getattr(getattr(resolved, 'peer', None), 'user_id', None) != item['seller_id']):
        raise ValueError("Username не соответствует владельцу")
    peer = types.InputPeerUser(user.id, user.access_hash)
    full = await account.limiter.call(lambda: account.client(functions.users.GetFullUserRequest(id=peer)), attempts=1)
    details = getattr(full, 'full_user', None)
    if (not details or details.id != user.id or getattr(details, 'blocked', False)
            or getattr(details, 'send_paid_messages_stars', None) or getattr(user, 'send_paid_messages_stars', None)):
        raise ValueError("Чат заблокирован или платные сообщения не поддерживаются")
    return result, peer


async def run(ledger: Ledger, store: Store, pool: SessionPool, settings: Settings, run_id: str, lock: asyncio.Lock) -> None:
    run_data = ledger.detail(run_id)
    if not run_data or run_data['status'] != 'running':
        return
    expected_buyer: int | None = None
    try:
        for item in run_data['rows']:
            wait = ledger.seconds_until_next()
            if wait:
                await asyncio.sleep(wait)
            async with lock:
                account = await _open_account(settings, store, pool, run_data['session'])
                try:
                    checked, peer = await _verify(store, account, item)
                    buyer_id = checked['account_id']
                    if expected_buyer is not None and buyer_id != expected_buyer:
                        raise ValueError("Telegram-аккаунт изменился во время партии")
                    expected_buyer = buyer_id
                    assert account.limiter is not None
                    await account.limiter.wait()
                    attempt_id, random_id = ledger.reserve(run_data, item, buyer_id)
                    status, text, mid = 'unknown', 'Ответ не получен; проверьте чат и не повторяйте', None
                    try:
                        response = await _send_once(account.client, peer, item['command'], random_id)
                        mid = message_id(response, random_id)
                        if mid:
                            status = 'telegram_ack'
                            text = 'Telegram принял сообщение; обработка ботом и создание оффера не подтверждены'
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 — any uncertain send stops the run
                        text = f"Исход неизвестен: {str(exc)[:160] or type(exc).__name__}; проверьте чат"
                    finally:
                        ledger.outcome(run_id, item['position'], attempt_id, status, text, mid)
                    if status != 'telegram_ack':
                        raise ValueError(text)
                finally:
                    await account.client.disconnect()
        ledger.finish(run_id, 'done')
    except asyncio.CancelledError:
        ledger.finish(run_id, 'stopped', 'Остановлено. Уже отправленный запрос отменить нельзя; проверьте журнал.')
        raise
    except Exception as exc:  # noqa: BLE001
        ledger.finish(run_id, 'error', str(exc)[:300] or type(exc).__name__)
