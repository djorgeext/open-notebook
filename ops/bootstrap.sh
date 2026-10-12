#!/usr/bin/env bash
# One-time (idempotent) setup inside the container: tools, deps, local AI servers, .env.
# Does NOT touch the GPU. Safe to re-run.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source ops/env.sh
say() { printf "\n== %s\n" "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

say "base tools"
for t in curl git python3; do have "$t" || { echo "missing $t (apt-get install -y $t)"; exit 1; }; done
have vllm || echo "WARN: vllm not on PATH (LLM service will fail)"
have ffmpeg || apt-get install -y --no-install-recommends ffmpeg || { apt-get update && apt-get install -y --no-install-recommends ffmpeg; }  # Speaches audio encode/decode

say "uv"
if ! have uv; then curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; fi
uv --version

say "node >= 20"
if ! have node || [[ "$(node -v | sed 's/v\([0-9]*\).*/\1/')" -lt 20 ]]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y --no-install-recommends nodejs
fi
node -v

say "SurrealDB v2"
if ! have surreal; then
  ver="$(curl -fsSL 'https://api.github.com/repos/surrealdb/surrealdb/releases?per_page=100' \
        | grep -o '"tag_name": *"v2\.[0-9]*\.[0-9]*"' | head -1 | grep -o 'v2[^"]*')"
  echo "installing surreal $ver"
  # installer default dir = /usr/local/bin; a trailing dir arg breaks its arg parser ("can't shift")
  curl -sSf https://install.surrealdb.com | sh -s -- --version "$ver"
fi
surreal version

say ".env"
[[ -f .env ]] || cp .env.example .env
SURREAL_PORT="$SURREAL_PORT" python3 - <<'PY'
import os, pathlib, re, secrets
p = pathlib.Path(".env"); txt = p.read_text()
want = {
    "SURREAL_URL": f"ws://127.0.0.1:{os.environ['SURREAL_PORT']}/rpc",
    "OPEN_NOTEBOOK_WORKER_MAX_TASKS": "1",
    "ESPERANTO_LLM_TIMEOUT": "600",
    "API_HOST": "127.0.0.1",
}
if re.search(r"^OPEN_NOTEBOOK_ENCRYPTION_KEY=change-me", txt, re.M):
    want["OPEN_NOTEBOOK_ENCRYPTION_KEY"] = secrets.token_urlsafe(32)
for k, v in want.items():
    if re.search(rf"^{k}=", txt, re.M):
        if k in ("SURREAL_URL", "OPEN_NOTEBOOK_ENCRYPTION_KEY"):
            txt = re.sub(rf"^{k}=.*$", f"{k}={v}", txt, flags=re.M)
    else:
        txt += f"\n{k}={v}"
p.write_text(txt.rstrip() + "\n")
print("ok .env (keep OPEN_NOTEBOOK_ENCRYPTION_KEY stable)")
PY

say "app deps"
uv sync
(cd frontend && npm install)

say "embedding server venv (CPU torch)"
if [[ ! -x $OPS_HOME/venv-embed/bin/python ]]; then
  uv venv $OPS_HOME/venv-embed --python 3.12
  uv pip install --python $OPS_HOME/venv-embed/bin/python torch --index-url https://download.pytorch.org/whl/cpu
  uv pip install --python $OPS_HOME/venv-embed/bin/python "sentence-transformers>=3" fastapi uvicorn numpy
fi

say "Speaches (TTS/STT, CPU)"
if [[ ! -d $OPS_HOME/speaches ]]; then
  mkdir -p "$OPS_HOME"
  git clone --depth 1 https://github.com/speaches-ai/speaches $OPS_HOME/speaches
fi
(cd $OPS_HOME/speaches && uv sync --frozen --no-dev)
# Speaches pins onnxruntime-gpu (CUDA) on x86_64 → swap to CPU build (MIT). Run outside its dir:
# its pyproject override-dependencies would otherwise filter onnxruntime out.
ORT_V="$($OPS_HOME/speaches/.venv/bin/python -c 'import importlib.metadata as m; print(m.version("onnxruntime-gpu"))' 2>/dev/null || true)"
if [[ -n $ORT_V ]]; then
  (cd / && uv pip uninstall --python $OPS_HOME/speaches/.venv/bin/python onnxruntime-gpu \
     && uv pip install --python $OPS_HOME/speaches/.venv/bin/python --no-config --reinstall "onnxruntime==$ORT_V")
fi

say "Chrome for headless tests (Playwright)"
npx -y playwright install --with-deps chrome || echo "WARN: chrome install failed; tester UI checks will be limited"

say "done"
echo "next: ops/stack.sh up && ops/stack.sh speech-models && ops/stack.sh wire"
