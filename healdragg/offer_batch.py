"""Bounded, persistent read-only verification of offer drafts.

No message/payment/offer mutation is performed. A restart always interrupts the
run; old observations are never treated as a licence to send.
"""
from __future__ import annotations

import asyncio
import json
from decimal import ROUND_CEILING, Decimal
from typing import Any
from uuid import uuid4

from . import offer_check, offers
from .db import Store
from .tg import SessionPool
from .util import now_ts


def interrupt_running(store: Store) -> None:
    with store.tx() as conn:
        conn.execute("UPDATE offer_check_items SET status='pending' WHERE status='checking'")
        conn.execute("UPDATE offer_check_runs SET status='interrupted', error=?, updated_at=? WHERE status='running'",
                     ("Проверка прервана. Запустите новую проверку; старые результаты могут устареть.", now_ts()))


def create(store: Store, body: Any) -> str:
    if not isinstance(body, dict) or body.keys() != {"session", "plan"}:
        raise ValueError("Укажите аккаунт и параметры черновика")
    session = body["session"]
    if not isinstance(session, str) or not session or len(session) > 200:
        raise ValueError("Выберите аккаунт предложений")
    account = store.get_session(session)
    if not account or account.get("purpose") != "offers" or account.get("status") != "active":
        raise ValueError("Выберите активный аккаунт предложений")
    # Always recalculate on the server, rather than trusting client-provided rows.
    plan = body["plan"]
    preview = offers.preview_batch(store, plan)
    if not preview["rows"]:
        raise ValueError("В черновике нет подходящих NFT для проверки")
    run_id, now = uuid4().hex, now_ts()
    with store.tx() as conn:
        if conn.execute("SELECT 1 FROM offer_check_runs WHERE status='running' LIMIT 1").fetchone():
            raise ValueError("Другая проверка уже выполняется")
        conn.execute("INSERT INTO offer_check_runs VALUES(?,?,?,?,?,?,?)",
                     (run_id, session, json.dumps(plan, ensure_ascii=False), "running", None, now, now))
        conn.executemany("INSERT INTO offer_check_items(run_id,position,slug,seller_id) VALUES(?,?,?,?)",
                         [(run_id, i, row["slug"], row["owner_tg_id"])
                          for i, row in enumerate(preview["rows"])])
    return run_id


def detail(store: Store, run_id: str | None = None) -> dict[str, Any] | None:
    run = (store.query_one("SELECT * FROM offer_check_runs WHERE id=?", (run_id,)) if run_id else
           store.query_one("SELECT * FROM offer_check_runs ORDER BY rowid DESC LIMIT 1"))
    if not run:
        return None
    run["settings"] = json.loads(run["settings"])
    rows = store.query("SELECT position,slug,seller_id,status,result,error FROM offer_check_items WHERE run_id=? ORDER BY position", (run["id"],))
    for row in rows:
        row["result"] = json.loads(row["result"]) if row["result"] else None
    run["rows"] = rows
    return run


def finish(store: Store, run_id: str, status: str, error: str | None = None) -> None:
    with store.tx() as conn:
        conn.execute("UPDATE offer_check_items SET status='pending' WHERE run_id=? AND status='checking'", (run_id,))
        conn.execute("UPDATE offer_check_runs SET status=?, error=?, updated_at=? WHERE id=? AND status='running'",
                     (status, error, now_ts(), run_id))


async def run(store: Store, pool: SessionPool, run_id: str, session_lock: asyncio.Lock) -> None:
    job = detail(store, run_id)
    if not job or job["status"] != "running":
        return
    settings = job["settings"]
    rate, markup = Decimal(settings["usd_per_unit"]), Decimal(settings["markup_percent"])
    total = 0
    try:
        for item in job["rows"]:
            store.execute("UPDATE offer_check_items SET status='checking' WHERE run_id=? AND position=?",
                          (run_id, item["position"]))
            # Serialize account import/login/business connection changes during each check.
            async with session_lock:
                result = await offer_check.check(store, pool, {"session": job["session"], "slug": item["slug"],
                                                              "seller_id": item["seller_id"]})
            bot = result["bot"]
            reasons = []
            if not result["owner_matches"] or not result["username_matches"]:
                reasons.append("владелец или username не подтверждён")
            if not result["premium"] or result["account_id"] == item["seller_id"]:
                reasons.append("аккаунт без Premium или совпадает с владельцем")
            if bot.get("status") != "connected" or bot.get("missing_rights") or not bot.get("all_chats"):
                reasons.append("проверьте подключение и права бота")
            value = result["value_usd_cents"]
            amount = None
            if value is None or value > 1_000_000_000_000:
                reasons.append("нет допустимой USD-оценки Telegram")
            else:
                amount = int((Decimal(value) / (100 * rate) * (1 + markup / 100)).to_integral_value(rounding=ROUND_CEILING))
                if amount > settings["max_per_offer"] or total + amount > settings["total_budget"]:
                    reasons.append("сумма выше лимита NFT или остатка бюджета")
            result["amount"] = amount
            result["currency"] = settings["currency"]
            result["reasons"] = reasons
            if not reasons:
                total += amount
            result["batch_total"] = total
            store.execute("UPDATE offer_check_items SET status=?,result=?,error=NULL WHERE run_id=? AND position=?",
                          ("skipped" if reasons else "checked", json.dumps(result, ensure_ascii=False),
                           run_id, item["position"]))
            store.execute("UPDATE offer_check_runs SET updated_at=? WHERE id=?", (now_ts(), run_id))
        finish(store, run_id, "done")
    except asyncio.CancelledError:
        finish(store, run_id, "stopped")
        raise
    except Exception as exc:  # noqa: BLE001 — pause batch on any unexpected/network failure
        finish(store, run_id, "error", str(exc)[:300] or type(exc).__name__)
