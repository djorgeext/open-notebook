#!/usr/bin/env bash
# Local stack manager — no docker, no systemd.
# Usage: ops/stack.sh up|down|restart|status|logs|gpu|speech-models|wire [svc...]
# Services (start order): surreal llm embed speech api worker web
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
source ops/env.sh
RUN="$ROOT/.run"
mkdir -p "$RUN" "$ROOT/surreal_data"
ALL=(surreal llm embed speech api worker web)

svc_cmd() {
  case $1 in
    surreal) echo "exec surreal start --log info --user ${SURREAL_USER:-root} --pass ${SURREAL_PASSWORD:-root} --bind 127.0.0.1:$SURREAL_PORT rocksdb:$ROOT/surreal_data/mydatabase.db" ;;
    llm)     echo "exec bash ops/serve_llm.sh" ;;
    embed)   echo "exec $OPS_HOME/venv-embed/bin/python ops/embed_server.py" ;;
    speech)  echo "cd $OPS_HOME/speaches && exec uv run --no-sync uvicorn --factory speaches.main:create_app --host 127.0.0.1 --port $SPEECH_PORT" ;;
    api)     echo "exec uv run --env-file .env run_api.py" ;;
    worker)  echo "exec uv run --env-file .env surreal-commands-worker --import-modules commands --max-tasks $OPEN_NOTEBOOK_WORKER_MAX_TASKS" ;;
    web)     echo "cd frontend && exec npm run dev -- -H 127.0.0.1 -p $WEB_PORT" ;;
    *) return 1 ;;
  esac
}

svc_health() {
  case $1 in
    surreal) echo "http://127.0.0.1:$SURREAL_PORT/health" ;;
    llm)     echo "http://127.0.0.1:$LLM_PORT/v1/models" ;;
    embed)   echo "http://127.0.0.1:$EMBED_PORT/v1/models" ;;
    speech)  echo "http://127.0.0.1:$SPEECH_PORT/health" ;;
    api)     echo "http://127.0.0.1:$API_PORT/health" ;;
    web)     echo "http://127.0.0.1:$WEB_PORT" ;;
    worker)  echo "" ;;
  esac
}

svc_timeout() { case $1 in llm) echo 900 ;; speech|embed|web) echo 300 ;; *) echo 120 ;; esac; }

is_running() { [[ -f "$RUN/$1.pid" ]] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }

is_healthy() {
  local url; url="$(svc_health "$1")"
  if [[ -z "$url" ]]; then is_running "$1"; else curl -sf -m 5 -o /dev/null "$url"; fi
}

start_svc() {
  local s=$1 cmd t
  cmd="$(svc_cmd "$s")" || { echo "unknown svc: $s"; return 1; }
  if is_running "$s"; then echo "$s: already running"; return 0; fi
  # setsid → own process group, so stop can kill the whole tree (npm/uv children).
  setsid nohup bash -c "$cmd" >"$RUN/$s.log" 2>&1 </dev/null &
  echo $! >"$RUN/$s.pid"
  t=$(svc_timeout "$s")
  printf "%s: starting (pid %s, wait ≤%ss) " "$s" "$(cat "$RUN/$s.pid")" "$t"
  for ((i = 0; i < t; i += 3)); do
    if ! is_running "$s"; then echo "DIED"; tail -n 25 "$RUN/$s.log"; return 1; fi
    if [[ $i -ge 3 ]] && is_healthy "$s"; then echo "UP"; return 0; fi
    sleep 3
  done
  echo "TIMEOUT (see ops/stack.sh logs $s)"; return 1
}

stop_svc() {
  local s=$1 pid
  if ! is_running "$s"; then rm -f "$RUN/$s.pid"; echo "$s: stopped"; return 0; fi
  pid="$(cat "$RUN/$s.pid")"
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  kill -KILL -- "-$pid" 2>/dev/null || true
  rm -f "$RUN/$s.pid"
  echo "$s: stopped"
}

status() {
  printf "%-8s %-8s %-9s %s\n" SVC PROC HEALTH CHECK
  for s in "${ALL[@]}"; do
    local p="down" h="-"
    is_running "$s" && p="running"
    if [[ $p == running ]]; then is_healthy "$s" && h="ok" || h="FAIL"; fi
    printf "%-8s %-8s %-9s %s\n" "$s" "$p" "$h" "$(svc_health "$s")"
  done
  echo "GPU allowed: $ALLOWED_GPU (HIP_VISIBLE_DEVICES=$HIP_VISIBLE_DEVICES)"
}

speech_models() {
  for m in "$TTS_MODEL" "$STT_MODEL"; do
    echo "download $m"
    curl -sf -X POST "http://127.0.0.1:$SPEECH_PORT/v1/models/$m" >/dev/null && echo "  ok" \
      || (cd $OPS_HOME/speaches && SPEACHES_BASE_URL="http://127.0.0.1:$SPEECH_PORT" uv run --no-sync speaches-cli model download "$m")
  done
}

cmd="${1:-status}"; shift || true
targets=("$@"); [[ ${#targets[@]} -eq 0 ]] && targets=("${ALL[@]}")

case $cmd in
  up)      for s in "${targets[@]}"; do start_svc "$s" || exit 1; done ;;
  down)    for ((i = ${#targets[@]} - 1; i >= 0; i--)); do stop_svc "${targets[$i]}"; done ;;
  restart) for ((i = ${#targets[@]} - 1; i >= 0; i--)); do stop_svc "${targets[$i]}"; done
           for s in "${targets[@]}"; do start_svc "$s" || exit 1; done ;;
  status)  status ;;
  logs)    for s in "${targets[@]}"; do echo "== $s"; tail -n "${N:-60}" "$RUN/$s.log" 2>/dev/null; done ;;
  gpu)     echo "allowed: $ALLOWED_GPU"; rocm-smi --showuse --showmemuse --showpids 2>/dev/null || rocm-smi ;;
  speech-models) speech_models ;;
  wire)    python3 ops/wire_models.py ;;
  *) echo "usage: $0 up|down|restart|status|logs|gpu|speech-models|wire [svc...]"; exit 1 ;;
esac
