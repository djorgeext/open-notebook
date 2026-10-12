#!/usr/bin/env bash
# Serve the local LLM with vLLM on the ONE allowed GPU. Foreground; ops/stack.sh runs it in background.
set -euo pipefail
source "$(dirname "$0")/env.sh"

extra=()
if [[ "$LLM_TEXT_ONLY" == "1" ]]; then
  extra+=(--limit-mm-per-prompt '{"image":0}')
fi

echo "GPU=$HIP_VISIBLE_DEVICES model=$LLM_MODEL name=$LLM_NAME port=$LLM_PORT max_len=$LLM_MAX_LEN util=$LLM_GPU_UTIL"
exec vllm serve "$LLM_MODEL" \
  --served-model-name "$LLM_NAME" \
  --host 127.0.0.1 \
  --port "$LLM_PORT" \
  --tensor-parallel-size 1 \
  --dtype bfloat16 \
  --max-model-len "$LLM_MAX_LEN" \
  --max-num-seqs "$LLM_MAX_SEQS" \
  --gpu-memory-utilization "$LLM_GPU_UTIL" \
  --enforce-eager \
  --enable-auto-tool-choice \
  --tool-call-parser gemma4 \
  --reasoning-parser gemma4 \
  --trust-remote-code \
  "${extra[@]}"
