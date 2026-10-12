---
name: stack
description: Run local stack (surreal, llm, embed, speech, api, worker, web) — start, stop, status, logs, health, GPU check, model wiring, browser tunnel.
---
# Stack (caveman)

First time: `bash ops/bootstrap.sh` (uv, node, surreal, speaches, embed venv, chrome, .env). Idempotent.

`ops/stack.sh <cmd> [svc...]`
- cmd: `up` `down` `restart` `status` `logs <svc>` `gpu` `speech-models` `wire`
- svc (start order): surreal llm embed speech api worker web
- no svc = all
- log `.run/<svc>.log`, pid `.run/<svc>.pid`

Health:
| svc | url |
|---|---|
| surreal | 127.0.0.1:8010/health |
| llm | 127.0.0.1:8011/v1/models |
| embed | 127.0.0.1:8002/v1/models |
| speech | 127.0.0.1:8969/health |
| api | 127.0.0.1:5055/health |
| web | 127.0.0.1:3000 |

- llm load ~50 GB → slow start, wait ≤15 min. Watch `ops/stack.sh logs llm`.
- llm OOM → lower LLM_MAX_LEN / LLM_MAX_SEQS in ops/env.sh. Still OOM → fallback `LLM_MODEL=google/gemma-4-E4B-it`.
- After first up: `ops/stack.sh speech-models` then `ops/stack.sh wire` (= `python3 ops/wire_models.py`, idempotent).
- api auto-reload on code change. worker + web need restart.
- GPU: `ops/stack.sh gpu`. Only allowed GPU busy.
- User browser: tunnel in AGENTS_SETUP.md → http://localhost:3000.

Stuck → read NOTES.md (same folder).
