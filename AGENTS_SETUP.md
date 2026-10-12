# Agent team + local stack — setup

Four Claude Code agents build this fork on one MI210 with a local Gemma 4 26B:

| Agent | Model | Job |
|---|---|---|
| orchestrator | Sonnet 5.5 (main session) | picks backlog items, routes work and bugs, talks to you |
| planner | Opus 5.5, effort high | writes `.agents/plans/<id>.md`, no code |
| executioner | Opus 5.5, effort high | implements, lints, tests, restarts services |
| tester | Opus 5.5, effort high | checks every feature by API + headless Chrome, files bugs, verifies fixes |

Prompts are written in "caveman" style to save tokens. All app AI work (embeddings, indexing, concept maps, podcast scripts) runs on the local models, never on Claude.

## 0. Put the files in your fork

From your laptop, copy the zip into the container, then unpack it at the repo root (made against upstream commit `a0f6f30`):

```bash
scp -J djorge@ssh.csc.gob.ar -P 2322 notebook-agents.zip root@sputnik.local:/app/
# inside the container
cd /app && git clone https://github.com/djorgeext/open-notebook.git && cd open-notebook
unzip -o ../notebook-agents.zip        # no unzip? python3 -m zipfile -e ../notebook-agents.zip . && chmod +x ops/*.sh ops/*.py .claude/hooks/*.py
```

Claude Code must be installed and logged in inside the container (`claude` prints a login URL you can open on your laptop).

## 1. Confirm the GPU

`ALLOWED_GPU=1` means HIP device index **1** (the second card in `rocm-smi`). If "#1" means the first card, change it to `0` in **both**:
- `ops/env.sh` → `ALLOWED_GPU`
- `.claude/settings.json` → `env.ALLOWED_GPU` and `env.HIP_VISIBLE_DEVICES`

A hook (`.claude/hooks/gpu_guard.py`) blocks agent commands that target another GPU, use tensor parallel > 1, change GPU state, or delete data dirs. Agents cannot edit the hook or settings.

## 2. Ports 8000/8001 are taken

Other apps on this box already hold 8000 (`pdf-rag-mcp-server`) and 8001 (a nomic-embed vLLM on GPU 0). Leave them running; the stack uses **8010** (SurrealDB) and **8011** (Gemma vLLM, allowed GPU). Change in `ops/env.sh` (`SURREAL_PORT`, `LLM_PORT`) if you ever free 8000/8001.

## 3. Install and start (inside the container, repo root)

```bash
bash ops/bootstrap.sh          # uv, node 22, SurrealDB v2, deps, CPU embed server, Speaches, Chrome, .env
ops/stack.sh up                # surreal → llm → embed → speech → api → worker → web (llm takes minutes)
ops/stack.sh speech-models     # Kokoro TTS + Whisper STT
ops/stack.sh wire              # point Open Notebook at the local models
ops/stack.sh status
```

Or start Claude and say `go F0` — the agents do these steps.

| Service | Port | Runs on |
|---|---|---|
| web (Next.js dev) | 3000 | CPU |
| api (FastAPI) | 5055 | CPU |
| surreal | 8010 | CPU |
| llm (vLLM, Gemma 4 26B BF16) | 8011 | the one GPU |
| embed (Qwen3-Embedding-0.6B) | 8002 | CPU |
| speech (Speaches: Kokoro + Whisper) | 8969 | CPU |

If the LLM runs out of memory: lower `LLM_MAX_LEN` (65536 → 32768) in `ops/env.sh`, or fall back with `LLM_MODEL=google/gemma-4-E4B-it ops/stack.sh restart llm`.

## 4. Open the app in your browser

On your laptop, open a tunnel (keep it running):

```bash
ssh -J djorge@ssh.csc.gob.ar -p 2322 -N \
  -L 3000:127.0.0.1:3000 -L 5055:127.0.0.1:5055 root@sputnik.local
```

Then browse **http://localhost:3000** (API docs: http://localhost:5055/docs). Forward **both** 3000 and 5055: the browser calls the API directly at `<host>:5055` (auto-detected by `/config`), not through the `/api` proxy, so with only 3000 forwarded the UI shows "Unable to Connect to API Server".

Optional `~/.ssh/config` so `ssh notebook` gives you a shell and the tunnel at once:

```
Host notebook
  HostName sputnik.local
  Port 2322
  User root
  ProxyJump djorge@ssh.csc.gob.ar
  LocalForward 3000 127.0.0.1:3000
  LocalForward 5055 127.0.0.1:5055
  ServerAliveInterval 30
```

If local port 3000 is busy, use `-L 13000:127.0.0.1:3000` and browse `localhost:13000` (keep `-L 5055:127.0.0.1:5055`; local port 5055 must still be free). If forwarding is refused, set `AllowTcpForwarding yes` in the container's `/etc/ssh/sshd_config` and restart sshd.

## 5. Run the team

```bash
cd <repo> && claude            # starts as orchestrator ("agent" in .claude/settings.json)
```

Useful prompts: `go F0`, `next`, `status`, `do F5`, `add feature: <idea>`.

Shared state lives in `.agents/`: `BACKLOG.md` (features), `plans/`, `bugs.md`, `LOG.md`. The tester keeps reusable checks in `e2e/` and screenshots in `.agents/evidence/`.

## Files

```
CLAUDE.md                     imports AGENTS.md + .claude/RULES.md
.claude/RULES.md              box rules for every agent
.claude/settings.json         main agent, GPU env, permissions, guard hook
.claude/hooks/gpu_guard.py    blocks other-GPU / multi-GPU / data deletion
.claude/agents/               orchestrator, planner, executioner, tester
.claude/skills/stack/         start/stop/health (+ NOTES.md for learned quirks)
.claude/skills/local-llm/     endpoints, GPU budget, how app code calls local models
.claude/skills/feature-check/ tester checklist, bug format
ops/                          env.sh, bootstrap.sh, stack.sh, serve_llm.sh, embed_server.py, wire_models.py
.agents/                      backlog, plans, bugs, log
```

The upstream `harness-qa` and `smoke-e2e` agents are kept as references; the team doesn't spawn them.
