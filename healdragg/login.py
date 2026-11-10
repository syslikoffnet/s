"""Вход в Telegram прямо из приложения: телефон → код → пароль 2FA.

Раньше сессию можно было создать только командой ``healdragg session add``.
Теперь тот же процесс доступен панели: она держит недолговечные «потоки входа»
(номер + отправленный код) и по завершении сохраняет ``.session`` в
``settings.sessions_dir``, а запись о сессии — в базе.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

from . import __version__
from .config import Settings
from .db import Store

log = logging.getLogger("healdragg.login")

#: Сколько живёт начатый вход, если пользователь не ввёл код.
FLOW_TTL = 15 * 60


class LoginError(Exception):
    """Ошибка входа, которую можно показать человеку."""


@dataclass
class Flow:
    """Один начатый вход в аккаунт."""

    id: str
    name: str
    phone: str
    created_at: float
    client: TelegramClient
    phone_code_hash: str | None = None
    password_needed: bool = False
    status: str = "code_sent"
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "phone": self.phone,
            "status": self.status,
            "password_needed": self.password_needed,
        }


class LoginManager:
    """Потоки входа: start → code → (password) → готовая сессия."""

    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store
        self._flows: dict[str, Flow] = {}

    # ── шаги ────────────────────────────────────────────────────────────────
    async def start(self, phone: str, name: str = "main") -> dict[str, Any]:
        self._cleanup()
        phone = (phone or "").strip().replace(" ", "")
        if not phone:
            raise LoginError("Нужен номер телефона")
        if not phone.startswith("+") and phone.lstrip("-").isdigit():
            phone = "+" + phone.lstrip("-")
        if not self.settings.has_api_credentials:
            raise LoginError("Сначала задай API_ID и API_HASH")
        name = (name or "main").strip() or "main"
        known = {str(row["name"]): row for row in self.store.list_sessions()}
        existing = known.get(name)
        if existing is not None and str(existing.get("status") or "") == "active":
            base, index = name, 2
            while f"{base}{index}" in known:
                index += 1
            name = f"{base}{index}"

        path = self.settings.session_file(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Перелогин в неактивный аккаунт: старый файл может держать битую авторизацию.
        if existing is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                log.warning("не удалось удалить старый файл сессии %s", path)
        client = TelegramClient(
            str(path),
            int(self.settings.api_id or 0),
            self.settings.api_hash,
            device_model="Healdragg",
            system_version="Android 14",
            app_version=__version__,
            lang_code="ru",
            system_lang_code="ru",
        )
        try:
            await client.connect()
            sent = await client.send_code_request(phone)
        except FloodWaitError as exc:
            await self._safe_disconnect(client)
            seconds = int(getattr(exc, "seconds", 0))
            raise LoginError(f"Telegram просит подождать {seconds} с") from exc
        except PhoneNumberInvalidError as exc:
            await self._safe_disconnect(client)
            raise LoginError("Telegram не принял этот номер") from exc
        except Exception as exc:  # noqa: BLE001
            await self._safe_disconnect(client)
            raise LoginError(f"Не удалось отправить код: {exc}") from exc

        flow = Flow(
            id=uuid.uuid4().hex[:12],
            name=name,
            phone=phone,
            created_at=time.time(),
            client=client,
            phone_code_hash=getattr(sent, "phone_code_hash", None),
        )
        self._flows[flow.id] = flow
        log.info("вход: код отправлен на %s (поток %s)", phone, flow.id)
        return flow.as_dict()

    async def submit_code(self, flow_id: str, code: str) -> dict[str, Any]:
        flow = self._flow(flow_id)
        digits = "".join(ch for ch in (code or "") if ch.isdigit())
        if not digits:
            raise LoginError("Введи код из Telegram")
        try:
            await flow.client.sign_in(
                phone=flow.phone,
                code=digits,
                phone_code_hash=flow.phone_code_hash,
            )
        except SessionPasswordNeededError:
            flow.password_needed = True
            flow.status = "password_needed"
            return flow.as_dict()
        except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
            raise LoginError("Код не подошёл или истёк") from exc
        except FloodWaitError as exc:
            seconds = int(getattr(exc, "seconds", 0))
            raise LoginError(f"Подожди {seconds} с") from exc
        except Exception as exc:  # noqa: BLE001
            raise LoginError(f"Telegram отказал: {exc}") from exc
        return await self._finish(flow)

    async def submit_password(self, flow_id: str, password: str) -> dict[str, Any]:
        flow = self._flow(flow_id)
        if not (password or "").strip():
            raise LoginError("Введи пароль 2FA")
        try:
            await flow.client.sign_in(password=password.strip())
        except PasswordHashInvalidError as exc:
            raise LoginError("Неверный пароль 2FA") from exc
        except FloodWaitError as exc:
            seconds = int(getattr(exc, "seconds", 0))
            raise LoginError(f"Подожди {seconds} с") from exc
        except Exception as exc:  # noqa: BLE001
            raise LoginError(f"Telegram отказал: {exc}") from exc
        return await self._finish(flow)

    async def cancel(self, flow_id: str) -> None:
        flow = self._flows.pop(flow_id, None)
        if flow is not None:
            await self._safe_disconnect(flow.client)

    async def close(self) -> None:
        for flow_id in list(self._flows):
            await self.cancel(flow_id)

    # ── служебное ───────────────────────────────────────────────────────────
    async def _finish(self, flow: Flow) -> dict[str, Any]:
        try:
            me = await flow.client.get_me()
            if me is None:
                raise LoginError("Telegram не вернул профиль")
            self.store.add_session(
                flow.name,
                str(self.settings.session_file(flow.name)),
                tg_user_id=int(me.id),
                username=me.username,
                first_name=me.first_name,
                phone=getattr(me, "phone", None) or flow.phone,
                status="active",
            )
            self.store.log_event("info", f"сессия {flow.name} подключена", "app")
        finally:
            await self._safe_disconnect(flow.client)
            self._flows.pop(flow.id, None)
        flow.status = "done"
        log.info("вход: аккаунт %s подключён (%s)", flow.name, me.id)
        return {
            "id": flow.id,
            "status": "done",
            "name": flow.name,
            "tg_user_id": int(me.id),
            "username": me.username,
            "first_name": me.first_name,
        }

    def _flow(self, flow_id: str) -> Flow:
        flow = self._flows.get((flow_id or "").strip())
        if flow is None:
            raise LoginError("Вход истёк — начни заново")
        if time.time() - flow.created_at > FLOW_TTL:
            self._flows.pop(flow.id, None)
            raise LoginError("Вход истёк — начни заново")
        return flow

    def _cleanup(self) -> None:
        now = time.time()
        for flow_id, flow in list(self._flows.items()):
            if now - flow.created_at > FLOW_TTL:
                self._flows.pop(flow_id, None)

    @staticmethod
    async def _safe_disconnect(client: TelegramClient) -> None:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["LoginError", "LoginManager"]
