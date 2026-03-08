#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

VENV_PY="$ROOT_DIR/.venv/bin/python"
STATE_DIR="${TMPDIR:-/tmp}/sam-local-run"
LOG_DIR="$STATE_DIR/logs"
API_PID_FILE="$STATE_DIR/api.pid"
COLLECTOR_PID_FILE="$STATE_DIR/collector.pid"
DASHBOARD_PID_FILE="$STATE_DIR/dashboard.pid"

mkdir -p "$LOG_DIR"

API_LOG="$LOG_DIR/api.log"
COLLECTOR_LOG="$LOG_DIR/collector.log"
DASHBOARD_LOG="$LOG_DIR/dashboard.log"

usage() {
  echo "Usage: ./run-all-local.sh [start|stop|status|logs]"
  echo ""
  echo "  start  Start postgres+redis (docker) and api+collector+dashboard (.venv)"
  echo "  stop   Stop local api+collector+dashboard and docker postgres+redis"
  echo "  status Show status of local services"
  echo "  logs   Print log file locations"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[error] Missing required command: $1" >&2
    exit 1
  fi
}

is_pid_running() {
  local pid="$1"
  kill -0 "$pid" >/dev/null 2>&1
}

is_port_listening() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

find_pid_by_pattern() {
  local pattern="$1"
  local pids
  pids="$(pgrep -f "$pattern" || true)"
  if [[ -n "$pids" ]]; then
    printf '%s\n' "${pids%%$'\n'*}"
  fi
}

start_service() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"
  local port="$4"
  local process_pattern="$5"
  shift 5
  local cmd=("$@")

  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(<"$pid_file")"
    if [[ -n "$pid" ]] && is_pid_running "$pid"; then
      echo "[ok] $name already running (pid $pid)"
      return 0
    fi
    rm -f "$pid_file"
  fi

  if [[ -n "$process_pattern" ]]; then
    local existing_pid
    existing_pid="$(find_pid_by_pattern "$process_pattern")"
    if [[ -n "$existing_pid" ]]; then
      echo "$existing_pid" >"$pid_file"
      echo "[ok] $name already running (pid $existing_pid)"
      return 0
    fi
  fi

  if [[ -n "$port" ]] && is_port_listening "$port"; then
    echo "[warn] $name port $port is in use; skipping start"
    return 0
  fi

  nohup "${cmd[@]}" >"$log_file" 2>&1 &
  local pid="$!"
  echo "$pid" >"$pid_file"
  echo "[ok] started $name (pid $pid)"
}

stop_service() {
  local name="$1"
  local pid_file="$2"

  if [[ ! -f "$pid_file" ]]; then
    echo "[ok] $name not running via pidfile"
    return 0
  fi

  local pid
  pid="$(<"$pid_file")"
  if [[ -n "$pid" ]] && is_pid_running "$pid"; then
    kill "$pid" >/dev/null 2>&1 || true
    for _ in {1..20}; do
      if ! is_pid_running "$pid"; then
        break
      fi
      sleep 0.2
    done
    if is_pid_running "$pid"; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
    echo "[ok] stopped $name (pid $pid)"
  else
    echo "[ok] $name pidfile stale"
  fi

  rm -f "$pid_file"
}

wait_for_http() {
  local url="$1"
  local timeout_s="$2"
  local name="$3"

  for ((i=1; i<=timeout_s; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "[ok] $name is reachable: $url"
      return 0
    fi
    sleep 1
  done

  echo "[warn] Timed out waiting for $name at $url"
  return 1
}

run_migrations() {
  echo "[step] Applying database migrations..."
  for ((i=1; i<=60; i++)); do
    if "$VENV_PY" -m alembic -c "$ROOT_DIR/alembic.ini" upgrade head >/dev/null 2>&1; then
      echo "[ok] Database migrations applied"
      return 0
    fi
    sleep 1
  done

  echo "[error] Failed to apply database migrations; retry output follows:" >&2
  "$VENV_PY" -m alembic -c "$ROOT_DIR/alembic.ini" upgrade head
}

start_all() {
  require_cmd docker
  require_cmd curl
  require_cmd lsof
  require_cmd pgrep

  if [[ ! -x "$VENV_PY" ]]; then
    echo "[error] Missing virtualenv python at $VENV_PY" >&2
    echo "Create it first (example): python -m venv .venv && .venv/bin/pip install -e \".[dev]\"" >&2
    exit 1
  fi

  echo "[step] Starting postgres + redis via docker compose..."
  docker compose up -d postgres redis >/dev/null

  run_migrations

  echo "[step] Starting local app services via .venv..."
  start_service \
    "api" \
    "$API_PID_FILE" \
    "$API_LOG" \
    "8000" \
    "uvicorn sam\\.api\\.main:app.*--port 8000" \
    "$VENV_PY" -m uvicorn sam.api.main:app --host 127.0.0.1 --port 8000

  start_service \
    "collector" \
    "$COLLECTOR_PID_FILE" \
    "$COLLECTOR_LOG" \
    "" \
    "sam\\.scheduler\\.runner" \
    "$VENV_PY" -m sam.scheduler.runner

  start_service \
    "dashboard" \
    "$DASHBOARD_PID_FILE" \
    "$DASHBOARD_LOG" \
    "8501" \
    "streamlit run src/dashboard/app\\.py.*--server\\.port 8501" \
    "$VENV_PY" -m streamlit run src/dashboard/app.py --server.address 127.0.0.1 --server.port 8501

  echo "[step] Waiting for health endpoints..."
  wait_for_http "http://127.0.0.1:8000/ready" 60 "API" || true
  wait_for_http "http://127.0.0.1:8501/_stcore/health" 60 "Dashboard" || true

  echo ""
  echo "SAM local stack is ready."
  echo "Project dir: $ROOT_DIR"
  echo "Dashboard:   http://127.0.0.1:8501"
  echo "API:         http://127.0.0.1:8000"
  echo ""
  echo "Logs:"
  echo "  API:       $API_LOG"
  echo "  Collector: $COLLECTOR_LOG"
  echo "  Dashboard: $DASHBOARD_LOG"
  echo ""
  echo "Use './run-all-local.sh stop' to stop local services."
}

stop_all() {
  echo "[step] Stopping local app services..."
  stop_service "dashboard" "$DASHBOARD_PID_FILE"
  stop_service "collector" "$COLLECTOR_PID_FILE"
  stop_service "api" "$API_PID_FILE"

  echo "[step] Stopping postgres + redis via docker compose..."
  docker compose stop postgres redis >/dev/null || true
  echo "[ok] Stopped."
}

status_all() {
  echo "== docker services (postgres/redis) =="
  docker compose ps postgres redis || true
  echo ""
  echo "== local app services =="
  for entry in "api:$API_PID_FILE" "collector:$COLLECTOR_PID_FILE" "dashboard:$DASHBOARD_PID_FILE"; do
    local name="${entry%%:*}"
    local file="${entry##*:}"
    if [[ -f "$file" ]]; then
      local pid
      pid="$(<"$file")"
      if [[ -n "$pid" ]] && is_pid_running "$pid"; then
        echo "$name: running (pid $pid)"
      else
        echo "$name: pidfile stale"
      fi
    else
      echo "$name: not tracked"
    fi
  done
}

show_logs() {
  echo "API log:       $API_LOG"
  echo "Collector log: $COLLECTOR_LOG"
  echo "Dashboard log: $DASHBOARD_LOG"
}

main() {
  local cmd="${1:-start}"
  case "$cmd" in
    start) start_all ;;
    stop) stop_all ;;
    status) status_all ;;
    logs) show_logs ;;
    -h|--help|help) usage ;;
    *)
      echo "[error] Unknown command: $cmd" >&2
      usage
      exit 1
      ;;
  esac
}

main "${1:-start}"
