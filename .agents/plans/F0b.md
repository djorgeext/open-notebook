# F0b — "Embed Content" does nothing (PDF-by-URL source)

goal: embed job for source finish in minutes, UI show progress/result, search find source.

## Root cause (found, evidence)
Not frontend wiring, not API. Button → `POST /api/embed` 200 → `Source.vectorize()` → `embed_source` job queued OK (api.log 22:59:02, 22:59:36, 23:04:21, 23:07:09). Job dies in worker:

1. **Client timeout too short.** `ESPERANTO_EMBEDDING_TIMEOUT` unset → esperanto default 60s (`.venv/.../esperanto/utils/timeout.py:10`). Batch = 50 chunks x ~1100 chars (`OPEN_NOTEBOOK_EMBEDDING_BATCH_SIZE` default 50). CPU server slower than 60s → httpx timeout, empty msg. 3 inner retries (`open_notebook/utils/embedding.py:46`) = ~184s per attempt, then surreal-commands retry x5. worker.log:
   `RuntimeError: Failed to generate embeddings using model 'local-embed' (batch 1/2, 50 texts): Failed to generate embeddings: ` (empty = timeout).
2. **Embed server meltdown.** `ops/embed_server.py` sync handler, no lock, torch default threads (128 cores). Timed-out requests keep running server-side; every retry adds concurrent `model.encode` → 2433 threads, 3600% CPU, RSS 11.8 GB. Measured: 1 text x 1100 chars = 151 s; 8 texts > 400 s. Cascade, never recover.
3. **UI no feedback.** `SourceDetailContent.tsx:275 handleEmbedContent` toast "Source embedding job submitted", refetch once, re-enable button. No job poll, no fail toast. "Content Not Embedded" stay forever. User click again → duplicate jobs (5 embed_source jobs for one source; WORKER_MAX_TASKS=1 → queue blocked ~1 h).
4. Side: `CommandService.cancel_command_job` + `list_command_jobs` are stubs (`api/command_service.py:71-92`) — cancel no-op. Not fixing here (upstream scope), only note.

Job status now: `command:ioe9cemxmckwyhbhpvtg` failed, `command:ux69krw335flr0u6q3sd` running, `nshmkdl9gowz1voxxgsm` / `35mzjeq10sa4n2kev2xd` / `rvybng55ysf341jqv434` new. Source `source:v4e42282fh9zk3gfhxum` full_text 88728 chars → 88 chunks.

## Steps
0. **Ops cleanup (before code).** Stop worker + embed (`ops/stack.sh` skill `stack`). Mark stale `embed_source` jobs above as failed in SurrealDB (first `SELECT * FROM command:nshmkdl9gowz1voxxgsm` to confirm field names, then `UPDATE` status only those ids). No delete of surreal_data.
1. **Spike (10 min).** With fixed server (step 2), time `POST :8002/v1/embeddings` for 16 and 50 texts x 1100 chars at EMBED_THREADS 8/16/32. Pick threads + batch so one request < 1/3 of timeout. Write numbers in LOG.md line.
2. `ops/embed_server.py`:
   - `torch.set_num_threads(int(os.environ.get("EMBED_THREADS", "16")))` at start (+ `OMP_NUM_THREADS` same value before torch import).
   - module `threading.Lock()`; wrap `model.encode` + token count in `with _lock:` → one encode at a time, no oversubscription.
   - `model.max_seq_length = int(os.environ.get("EMBED_MAX_LEN", "8192"))` (guard against huge single input).
3. `ops/env.sh` (committed; `.env` is gitignored): export
   - `EMBED_THREADS` (spike value, default 16)
   - `ESPERANTO_EMBEDDING_TIMEOUT=300`
   - `OPEN_NOTEBOOK_EMBEDDING_BATCH_SIZE=16` (or spike value)
   Make sure `.env` does not set these (it does not now). api + worker inherit from stack.sh env.
4. `open_notebook/utils/embedding.py` ~line 195: error msg use `f"{type(e).__name__}: {e}"` and also root cause `e.__cause__` type when str empty → log show `ReadTimeout` not blank. Test in `tests/test_embedding.py`: aembed raise `RuntimeError("x")` from `TimeoutError()` → message contains "TimeoutError".
5. `frontend/src/components/sources/SourceDetailContent.tsx` `handleEmbedContent`: reuse insight pattern (line ~170-215):
   - after `embedContent` return `command_id` → toast `t('sources.embeddingStarted')`, keep `isEmbedding` true, `insightsApi.waitForCommand(command_id, {intervalMs: 3000, signal})` (AbortController ref, abort on unmount like `insightPollingRef`).
   - completed but `status.result?.success === false` (embed commands catch ValueError, return success=False — see open_notebook/AGENTS.md) → treat as failed, show `result.error_message`.
   - completed → `refetchSource()` + `queryClient.invalidateQueries({queryKey: ['sources']})` + toast success `t('sources.embeddingDone')`.
   - failed/canceled/null → toast error with `getApiErrorMessage(status?.error_message ...)` (`t('sources.embeddingFailed')` fallback), `isEmbedding` false.
   - both embed buttons (line ~498 menu, ~695 alert) already `disabled={isEmbedding}` → no duplicate click while job runs.
   - move `waitForCommand`/`getCommandStatus` not needed; import from `@/lib/api/insights` as is (reuse).
6. i18n: add `sources.embeddingStarted`, `sources.embeddingDone`, `sources.embeddingFailed` to all 14 locales in `frontend/src/lib/locales/*/index.ts` (en-US reference; parity test).
7. Frontend test: `SourceDetailContent` test (existing file if any, else new `*.test.tsx`) — mock `embedContent` → `{command_id}`, mock `waitForCommand` → `{status:'failed', error_message:'boom'}` → error toast called, button enabled again; `completed` → refetch called.
8. CHANGELOG `## [Unreleased]` → `### Fixed`: "Embed Content on a source now shows job progress and reports failures instead of silently doing nothing." (one line; ops tuning not user-visible upstream).
9. Restart embed, api, worker (skill `stack`). Re-run embed on `source:v4e42282fh9zk3gfhxum`.

## Deps
None new. (torch, sentence-transformers already in venv-embed.)

## VRAM / RAM
GPU 0 change. Embed CPU: 1 encode at a time, threads = EMBED_THREADS (16 default). RAM ~2-3 GB RSS (was 11.8 GB under thread storm).

## Checks (tester)
1. `ps -o nlwp= -p $(cat .run/embed.pid)` after an embed job → threads < 200 (was 2433).
2. `time curl -s -XPOST 127.0.0.1:8002/v1/embeddings -H 'content-type: application/json' -d '{"input":[<16 x 1100-char strings>],"model":"local-embed"}'` → 200, < 60 s, 16 vectors len 1024.
3. `GET /api/commands/jobs/command:nshmkdl9gowz1voxxgsm` (and other stale ids) → not `new`/`running`.
4. API: `POST /api/embed {"item_id":"source:v4e42282fh9zk3gfhxum","item_type":"source"}` → 200 + `command_id`; poll `GET /api/commands/jobs/{id}` → `completed` within 10 min; `GET /api/sources/source:v4e42282fh9zk3gfhxum` → `embedded: true`, `embedded_chunks: 88` (± chunker).
5. UI: new source via link `https://arxiv.org/pdf/2608.18711` (or any small PDF URL), embed off at add. Open source → Details → "Embed Content" → button shows "Embedding..." + disabled while job runs → toast done → "Content Not Embedded" alert gone, menu says "Already Embedded".
6. UI fail path: stop embed svc, click Embed Content → after retries, error toast with message (not blank), button enabled again. Start embed back.
7. Search: `POST /api/search {"query":"heart rate variability egocentric video","type":"vector","limit":5,"search_sources":true,"search_notes":false}` → hit with `source:v4e42282fh9zk3gfhxum`.
8. worker.log on forced timeout (check 6) shows exception type (`ReadTimeout`/`TimeoutError`), not empty after colon.
9. `uv run ruff check . && uv run ruff format --check . && uv run python -m mypy . && uv run pytest tests/test_embedding.py`; in `frontend/`: `npm run lint && npm run test && npm run build` → all pass.

## Risks
- CPU speed unknown till spike; if 16 texts still > 100 s, lower batch to 8 and/or raise timeout. Long PDFs (500+ chunks) = many minutes; acceptable (async job), UI poll stay alive while page open only.
- Box shared with other apps (nomic vLLM, pdf-rag) → too many threads steal CPU; keep EMBED_THREADS modest.
- `waitForCommand` stops after 3 consecutive poll errors → UI fallback: refetch source on return null.
- Leaving page mid-job: no toast, but next open shows real `embedded` state (fine).
- Command cancel endpoint is a stub — stale jobs need manual DB update (step 0). Upstream issue, out of scope.
- `uv --env-file .env`: if someone adds these vars to `.env` later, values may differ from ops/env.sh — doc in ops/env.sh comment.
