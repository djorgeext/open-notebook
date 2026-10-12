# Local stack config — sourced by every ops/ script. One place to change.
# ALLOWED_GPU must match "env" in .claude/settings.json.

export ALLOWED_GPU="${ALLOWED_GPU:-1}"          # the ONE GPU this box may use (HIP index)
export HIP_VISIBLE_DEVICES="$ALLOWED_GPU"
unset CUDA_VISIBLE_DEVICES ROCR_VISIBLE_DEVICES HSA_OVERRIDE_GFX_VERSION
export VLLM_ROCM_USE_AITER=0                    # AITER is MI300-only; MI210 = gfx90a
export USE_ROCM_AITER_ROPE_BACKEND=0

# LLM (vLLM, GPU). Served name stays "local-llm" whatever model is behind it.
export LLM_MODEL="${LLM_MODEL:-google/gemma-4-26B-A4B-it}"   # fallback: google/gemma-4-E4B-it
export LLM_NAME="${LLM_NAME:-local-llm}"
export LLM_PORT="${LLM_PORT:-8011}"            # 8001 taken by another app on this box (nomic vLLM)
export LLM_MAX_LEN="${LLM_MAX_LEN:-65536}"
export LLM_MAX_SEQS="${LLM_MAX_SEQS:-8}"
export LLM_GPU_UTIL="${LLM_GPU_UTIL:-0.92}"
export LLM_TEXT_ONLY="${LLM_TEXT_ONLY:-1}"     # 1 = no image inputs (saves VRAM)

# Embeddings (CPU)
export EMBED_MODEL="${EMBED_MODEL:-Qwen/Qwen3-Embedding-0.6B}"
export EMBED_NAME="${EMBED_NAME:-local-embed}"
export EMBED_PORT="${EMBED_PORT:-8002}"
# CPU threads for the embed server (box is shared; drop to 24 if load high). Spike 2026-10-11,
# 16 texts x 1100 chars: 8 thr 26s, 16 thr 15s, 32 thr 9s. F0d: 24-chunk paper 55.6 s @16 -> 32.
# GPU embed rejected: needs LLM_GPU_UTIL <= 0.87 (Gemma KV budget).
export EMBED_THREADS="${EMBED_THREADS:-32}"
# Big requests encode in slices of EMBED_MICRO_BATCH; requests with <= EMBED_PRIORITY_MAX_TEXTS
# texts (search queries) jump the queue. 2026-10-12: search mid-job 4-10s (was 25-79s), 88-chunk job 230s.
export EMBED_MICRO_BATCH="${EMBED_MICRO_BATCH:-4}"
export EMBED_PRIORITY_MAX_TEXTS="${EMBED_PRIORITY_MAX_TEXTS:-2}"
# Bounded priority: after EMBED_HIGH_BURST small requests in a row, a waiting big slice runs (no starvation).
export EMBED_HIGH_BURST="${EMBED_HIGH_BURST:-3}"
# App side (api + worker inherit via stack.sh). `uv --env-file .env` overrides these
# if .env ever sets them — keep them out of .env.
export ESPERANTO_EMBEDDING_TIMEOUT="${ESPERANTO_EMBEDDING_TIMEOUT:-300}"
export OPEN_NOTEBOOK_EMBEDDING_BATCH_SIZE="${OPEN_NOTEBOOK_EMBEDDING_BATCH_SIZE:-16}"

# Speech (Speaches, CPU)
export SPEECH_PORT="${SPEECH_PORT:-8969}"
export TTS_MODEL="${TTS_MODEL:-speaches-ai/Kokoro-82M-v1.0-ONNX}"
export STT_MODEL="${STT_MODEL:-Systran/faster-whisper-small}"

# Where vendored servers + venvs live (outside the repo: keeps ruff/mypy/git clean)
export OPS_HOME="${OPS_HOME:-/opt/notebook-ops}"

# App
export SURREAL_PORT="${SURREAL_PORT:-8010}"    # 8000 taken by another app on this box (pdf-rag)
export API_PORT="${API_PORT:-5055}"
export WEB_PORT="${WEB_PORT:-3000}"
export OPEN_NOTEBOOK_WORKER_MAX_TASKS="${OPEN_NOTEBOOK_WORKER_MAX_TASKS:-1}"
