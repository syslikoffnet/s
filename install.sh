#!/usr/bin/env bash
# Healdragg — установка для Termux (Android), Linux и macOS.
#   curl -fsSL https://raw.githubusercontent.com/syslikoffnet/s/main/install.sh | bash
# или локально:
#   bash install.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; CYAN=$'\033[36m'; OFF=$'\033[0m'
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$GREEN" "$OFF" "$*"; }
warn() { printf '%s!%s %s\n' "$YELLOW" "$OFF" "$*"; }
die()  { printf '%s✗%s %s\n' "$RED" "$OFF" "$*" >&2; exit 1; }

IS_TERMUX=0
if [ -n "${TERMUX_VERSION:-}" ] || [[ "${PREFIX:-}" == *"com.termux"* ]]; then
  IS_TERMUX=1
fi

say "${BOLD}🩺 Healdragg — установка${OFF}"
say "${DIM}$ROOT${OFF}"
say ""

# ── 1. системные пакеты ────────────────────────────────────────────────────
if [ "$IS_TERMUX" = "1" ]; then
  say "${CYAN}Termux: обновляю пакеты${OFF}"
  pkg update -y >/dev/null 2>&1 || true
  # rust/clang нужны, только если pydantic-core придётся собирать из исходников
  pkg install -y python clang rust binutils libffi openssl libandroid-support \
                 make pkg-config termux-api >/dev/null 2>&1 || {
    warn "часть пакетов не установилась — пробую минимум"
    pkg install -y python >/dev/null
  }
  ok "python $(python -V 2>&1 | awk '{print $2}')"
  command -v termux-wake-lock >/dev/null 2>&1 && ok "termux-api на месте" || warn "termux-api не установлен (уведомления/батарея)"

  export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-2}"
  export TMPDIR="${TMPDIR:-$PREFIX/tmp}"
  export ANDROID_API_LEVEL="${ANDROID_API_LEVEL:-24}"
else
  command -v python3 >/dev/null 2>&1 || die "нужен python3 ≥ 3.10"
  ok "python $(python3 -V 2>&1 | awk '{print $2}')"
fi

PY=python3
[ "$IS_TERMUX" = "1" ] && PY=python
$PY - <<'PYCHECK' || die "нужен Python 3.10+"
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PYCHECK

# ── 2. виртуальное окружение ───────────────────────────────────────────────
if [ ! -d .venv ]; then
  say "${CYAN}Создаю .venv${OFF}"
  $PY -m venv .venv || die "не удалось создать venv (Termux: pkg install python)"
fi
# shellcheck disable=SC1091
. .venv/bin/activate

pip install --upgrade pip wheel setuptools >/dev/null 2>&1 || warn "pip не обновился"

# ── 3. зависимости ─────────────────────────────────────────────────────────
say "${CYAN}Ставлю ядро (telethon + rich)${OFF}"
pip install --no-cache-dir "telethon>=1.36" "rich>=13.9" "python-dotenv>=1.0" || die "ядро не установилось"

say "${CYAN}Ставлю бота и панель (aiogram + fastapi)${OFF}"
if ! pip install --no-cache-dir "aiogram>=3.20" "fastapi>=0.110" "uvicorn>=0.27"; then
  warn "pydantic-core не собрался."
  if [ "$IS_TERMUX" = "1" ]; then
    warn "Попробуй: pkg install tur-repo && pkg install python-pydantic"
    warn "или поставь pydantic вручную и повтори: pip install aiogram fastapi uvicorn"
  fi
  warn "CLI и TUI работать будут, бот и веб-панель — нет."
fi

# ── 4. сам пакет ───────────────────────────────────────────────────────────
say "${CYAN}Ставлю healdragg${OFF}"
pip install --no-deps -e . >/dev/null 2>&1 || pip install --no-deps --no-build-isolation -e . || die "пакет не установился"
ok "healdragg $(healdragg --version 2>/dev/null || echo '')"

# ── 5. конфиг ──────────────────────────────────────────────────────────────
mkdir -p data/sessions
if [ ! -f .env ]; then
  cp .env.example .env
  ok "создан .env (заполни API_ID / API_HASH / BOT_TOKEN)"
else
  ok ".env уже есть"
fi

# ── 6. обёртка healdragg ───────────────────────────────────────────────────
BIN_DIR=""
if [ "$IS_TERMUX" = "1" ] && [ -n "${PREFIX:-}" ]; then
  BIN_DIR="$PREFIX/bin"
else
  BIN_DIR="$HOME/.local/bin"
fi
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/healdragg" <<EOF
#!/usr/bin/env bash
# автогенерировано install.sh
cd "$ROOT" || exit 1
exec "$ROOT/.venv/bin/healdragg" "\$@"
EOF
chmod +x "$BIN_DIR/healdragg"
ok "команда: $BIN_DIR/healdragg"

if [ "$IS_TERMUX" = "1" ]; then
  if ! grep -q 'healdragg' "$HOME/.bashrc" 2>/dev/null; then
    printf '\n# Healdragg\nalias hd="healdragg"\n' >> "$HOME/.bashrc"
  fi
  chmod +x "$ROOT/termux/termuxctl.sh" 2>/dev/null || true
  say ""
  say "${BOLD}Termux-подсказки${OFF}"
  say "  termux-wake-lock                        — не дать системе усыпить бота"
  say "  bash termux/termuxctl.sh start          — запустить бота и панель в фоне"
  say "  bash termux/termuxctl.sh status|logs    — состояние и логи"
  say "  bash termux/termuxctl.sh autostart      — автозапуск после перезагрузки"
fi

say ""
say "${GREEN}${BOLD}Готово.${OFF}"
say "  1. nano .env            → API_ID, API_HASH, BOT_TOKEN"
say "  2. healdragg demo       → посмотреть, как выглядит"
say "  3. healdragg session add main"
say "  4. healdragg bootstrap"
say "  5. healdragg bot        (или: healdragg tui / healdragg api)"
