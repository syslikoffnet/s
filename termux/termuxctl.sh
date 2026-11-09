#!/usr/bin/env bash
# Управление фоновыми процессами Healdragg в Termux.
#   bash termux/termuxctl.sh start [bot|api|all]
#   bash termux/termuxctl.sh stop|restart|status|logs|autostart|unautostart
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${HEALDRAGG_RUN_DIR:-$HOME/.healdragg}"
LOG_DIR="$ROOT/data/logs"
mkdir -p "$RUN_DIR" "$LOG_DIR"

BIN="$ROOT/.venv/bin/healdragg"
[ -x "$BIN" ] || BIN="$(command -v healdragg || true)"

wake()   { command -v termux-wake-lock   >/dev/null 2>&1 && termux-wake-lock    || true; }
sleep_() { command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock  || true; }
toast()  { command -v termux-toast       >/dev/null 2>&1 && termux-toast "$1"   || echo "$1"; }

start_one() {
  local name="$1"; shift
  local pidfile="$RUN_DIR/$name.pid" logfile="$LOG_DIR/$name.log"
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "• $name уже запущен (pid $(cat "$pidfile"))"
    return 0
  fi
  [ -n "$BIN" ] || { echo "✗ healdragg не найден. Запусти install.sh"; return 1; }
  nohup "$BIN" "$@" >>"$logfile" 2>&1 &
  echo $! > "$pidfile"
  echo "✓ $name → pid $(cat "$pidfile"), лог: $logfile"
}

stop_one() {
  local name="$1" pidfile="$RUN_DIR/$name.pid"
  if [ -f "$pidfile" ]; then
    local pid; pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      sleep 1
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
      echo "■ $name остановлен"
    else
      echo "• $name не работал"
    fi
    rm -f "$pidfile"
  else
    echo "• $name не запущен"
  fi
}

status_one() {
  local name="$1" pidfile="$RUN_DIR/$name.pid"
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "● $name  pid $(cat "$pidfile")"
  else
    echo "○ $name  остановлен"
  fi
}

case "${1:-start}" in
  start)
    wake
    case "${2:-all}" in
      bot) start_one bot bot ;;
      api) start_one api api --host 0.0.0.0 ;;
      all)
        start_one bot bot
        start_one api api --host 0.0.0.0
        ;;
      *) echo "usage: $0 start [bot|api|all]"; exit 2 ;;
    esac
    toast "Healdragg запущен"
    ;;
  stop)
    stop_one bot; stop_one api; sleep_; toast "Healdragg остановлен"
    ;;
  restart)
    "$0" stop; sleep 1; "$0" start "${2:-all}"
    ;;
  status)
    status_one bot; status_one api
    echo
    [ -f "$LOG_DIR/bot.log" ] && tail -n "${3:-5}" "$LOG_DIR/bot.log"
    ;;
  logs)
    tail -n "${3:-60}" -f "$LOG_DIR/${2:-bot}.log"
    ;;
  autostart)
    mkdir -p "$HOME/.termux/boot"
    cat > "$HOME/.termux/boot/healdragg.sh" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
sleep 20
bash "$ROOT/termux/termuxctl.sh" start all
EOF
    chmod +x "$HOME/.termux/boot/healdragg.sh"
    echo "✓ автозапуск: $HOME/.termux/boot/healdragg.sh"
    echo "  (нужен пакет termux-boot: pkg install termux-boot)"
    ;;
  unautostart)
    rm -f "$HOME/.termux/boot/healdragg.sh"
    echo "✓ автозапуск выключен"
    ;;
  *)
    echo "usage: $0 {start [bot|api|all]|stop|restart|status|logs [bot|api]|autostart|unautostart}"
    exit 2
    ;;
esac
