"""Конфигурация: .env + переменные окружения, без магии."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_ENV_FILES = (".env", "healdragg.env")


def _env_paths() -> list[Path]:
    paths: list[Path] = []
    explicit = os.getenv("HEALDRAGG_ENV")
    if explicit:
        paths.append(Path(explicit).expanduser())
    cwd = Path.cwd()
    paths.extend(cwd / name for name in DEFAULT_ENV_FILES)
    here = Path(__file__).resolve().parent.parent
    paths.extend(here / name for name in DEFAULT_ENV_FILES)
    return paths


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _int(name: str, default: int | None = None) -> int | None:
    raw = _str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = _str(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = _str(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "да", "y"}


def _ids(name: str) -> set[int]:
    raw = _str(name)
    out: set[int] = set()
    for chunk in raw.replace(";", ",").replace(" ", ",").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            out.add(int(chunk))
    return out


@dataclass(slots=True)
class Settings:
    """Все настройки приложения в одном месте."""

    home: Path = field(default_factory=lambda: Path.cwd())
    db_path: Path = field(default_factory=lambda: Path.cwd() / "data" / "healdragg.db")
    sessions_dir: Path = field(default_factory=lambda: Path.cwd() / "data" / "sessions")

    api_id: int | None = None
    api_hash: str = ""
    bot_token: str = ""
    bot_allowed_users: set[int] = field(default_factory=set)
    bot_allow_all: bool = False
    owner_id: int | None = None

    api_host: str = "0.0.0.0"
    api_port: int = 8080
    api_token: str = ""

    scan_min_delay: float = 1.4
    scan_jitter: float = 0.6
    scan_max_groups: int = 2000
    join_limit: int = 20
    meta_ttl_hours: float = 12.0
    search_queries: list[str] = field(default_factory=list)

    page_size: int = 15
    title_limit: int = 15
    tz: str = "Europe/Moscow"
    log_level: str = "INFO"

    env_file: Path | None = None

    # ── загрузка ────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, env_file: str | os.PathLike[str] | None = None) -> Settings:
        if env_file:
            load_dotenv(env_file, override=True)
            used: Path | None = Path(env_file)
        else:
            used = None
            for candidate in _env_paths():
                if candidate.is_file():
                    load_dotenv(candidate, override=False)
                    used = used or candidate

        home = Path(_str("HEALDRAGG_HOME", "data")).expanduser()
        if not home.is_absolute():
            base = used.parent if used else Path.cwd()
            home = (base / home).resolve()

        db_raw = Path(_str("DB_PATH", str(home / "healdragg.db"))).expanduser()
        db_path = db_raw if db_raw.is_absolute() else (home / db_raw)

        sessions_raw = Path(_str("SESSIONS_DIR", str(home / "sessions"))).expanduser()
        sessions_dir = sessions_raw if sessions_raw.is_absolute() else (home / sessions_raw)

        return cls(
            home=home,
            db_path=db_path.resolve(),
            sessions_dir=sessions_dir.resolve(),
            api_id=_int("API_ID"),
            api_hash=_str("API_HASH"),
            bot_token=_str("BOT_TOKEN"),
            bot_allowed_users=_ids("BOT_ALLOWED_USERS"),
            bot_allow_all=_bool("BOT_ALLOW_ALL", False),
            owner_id=_int("OWNER_ID"),
            api_host=_str("API_HOST", "0.0.0.0"),
            api_port=_int("API_PORT", 8080) or 8080,
            api_token=_str("API_TOKEN"),
            scan_min_delay=_float("SCAN_MIN_DELAY", 1.4),
            scan_jitter=_float("SCAN_JITTER", 0.6),
            scan_max_groups=_int("SCAN_MAX_GROUPS", 2000) or 2000,
            join_limit=_int("JOIN_LIMIT", 20) or 0,
            meta_ttl_hours=_float("META_TTL_HOURS", 12.0),
            search_queries=[q.strip() for q in _str("SEARCH_QUERIES").split(",") if q.strip()],
            page_size=_int("PAGE_SIZE", 15) or 15,
            title_limit=_int("TITLE_LIMIT", 15) or 15,
            tz=_str("TZ", "Europe/Moscow") or "Europe/Moscow",
            log_level=_str("LOG_LEVEL", "INFO").upper(),
            env_file=used,
        )

    # ── валидация / сервис ──────────────────────────────────────────────────
    def ensure_dirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    @property
    def has_api_credentials(self) -> bool:
        return bool(self.api_id and self.api_hash and len(self.api_hash) >= 16)

    def require_api(self) -> tuple[int, str]:
        if not self.has_api_credentials:
            raise SystemExit(
                "Не заданы API_ID / API_HASH.\n"
                "1) https://my.telegram.org → API development tools\n"
                "2) впиши в .env: API_ID=... API_HASH=...\n"
                "3) или запусти: healdragg init"
            )
        return int(self.api_id or 0), self.api_hash

    def require_bot(self) -> str:
        if not self.bot_token:
            raise SystemExit("Не задан BOT_TOKEN. Получи токен у @BotFather и добавь в .env")
        return self.bot_token

    def ensure_api_token(self) -> str:
        """Токен панели. Генерируется при первом обращении и сохраняется в .env."""
        if self.api_token:
            return self.api_token
        token = secrets.token_urlsafe(24)
        self.api_token = token
        target_env = self.env_file or (Path.cwd() / ".env")
        try:
            target_env.parent.mkdir(parents=True, exist_ok=True)
            with target_env.open("a", encoding="utf-8") as fh:
                fh.write(f"\nAPI_TOKEN={token}\n")
        except OSError:
            pass
        return token

    def session_file(self, name: str) -> Path:
        return self.sessions_dir / f"{name}.session"

    def relative(self, path: Path | str) -> str:
        try:
            return str(Path(path).resolve().relative_to(self.home))
        except ValueError:
            return str(path)


def load_settings(env_file: str | os.PathLike[str] | None = None) -> Settings:
    return Settings.load(env_file)
