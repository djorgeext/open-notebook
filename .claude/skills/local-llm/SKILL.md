---
name: local-llm
description: Local AI endpoints, model ids, GPU budget, how app code call local Gemma / embeddings / speech. Use when feature need AI or model config.
---
# Local AI (caveman)

Endpoints (OpenAI API, no key, values in ops/env.sh):
| what | base url | model id | where |
|---|---|---|---|
| LLM | http://127.0.0.1:8011/v1 | local-llm (= google/gemma-4-26B-A4B-it bf16) | vLLM ROCm, GPU |
| embed | http://127.0.0.1:8002/v1 | local-embed (= Qwen/Qwen3-Embedding-0.6B, 1024 dim) | CPU |
| TTS | http://127.0.0.1:8969/v1 | speaches-ai/Kokoro-82M-v1.0-ONNX | CPU |
| STT | http://127.0.0.1:8969/v1 | Systran/faster-whisper-small | CPU |

App wiring: provider `openai_compatible`. `ops/stack.sh wire` set credentials + models + defaults. Check `GET /api/models/defaults`.

New AI code in app:
- Use project layer: `provision_langchain_model` (open_notebook/ai/provision.py). All via Esperanto. No new SDK.
- JSON out: vLLM structured output (`response_format` json_schema). Validate pydantic. Retry 1x.
- Tool call: gemma4 parser can break JSON → prefer json_schema over tools.
- Bulk job (index, chunk context, concept map, entity, summary) → worker command in commands/, not request path. WORKER_MAX_TASKS=1.
- Prompt + doc > LLM_MAX_LEN → chunk / map-reduce.
- Thinking: off for bulk job (check gemma4 chat template kwarg first).
- Timeout ESPERANTO_LLM_TIMEOUT=600.

GPU budget (1x MI210 64GB):
- Gemma 26B bf16 ≈ 48 GiB weights + KV, util 0.92 → ~5 GB left.
- Rest CPU by default: embed, rerank, ASR, TTS, NLI verifier.
- Want extra GPU model → measure free VRAM (`ops/stack.sh gpu`). No fit → CPU, or time-swap (stop llm, run, start llm). Never second GPU.

Never: fine-tune, cloud AI key, Claude doing app AI work.
