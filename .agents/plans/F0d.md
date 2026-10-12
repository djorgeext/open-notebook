# F0d — arxiv 2610.00088 "processing never ends"

goal: link source card goes processing → done by itself (no reload), and pipeline for this paper < 2 min.

## Findings (2026-10-12 03:10, read-only, DB not touched)
Backend NOT stuck. All jobs done in ~113 s:
| step | command | time |
|---|---|---|
| POST /api/sources | source:ac4isz34prb6ftb9pak3 | 02:45:37 |
| process_source (extract 1.5 s, content-core, 25 529 chars; Dense Summary transformation on Gemma ~48 s) | command:rb3pmgczi9zadjrc74b5 completed | 49.3 s |
| embed_source (24 chunks, avg 1150 ch, CPU 16 thr = 2.3 s/chunk) | command:cfi2iunaw4j43a9iferb completed | 55.6 s |
| create_insight | command:9s9oe6pjv99r08rrmnn4 completed | 0.07 s |
| embed_insight | command:ok22h8e82iu1ickbdwl1 completed | 6.9 s, done 02:47:30 |
- Stuck job ids: NONE. `SELECT ... FROM command WHERE status IN ['new','running','queued']` → []. source_embedding rows = 24, insight 1. Embed svc /health ok, idle.
- Fresh page load (headless chrome, 03:12) → card done, Details "Embedded". So DB/API say done.
- **Real bug = UI.** api.log: browser polled `GET /api/sources/source:ac4...status` 18x after POST (~36 s at 2 s), then NO more requests from user tab at all (no status, no list refetch) though job finished 02:46:27 / 02:47:30. Card keeps spinner until reload → user sees "never ends" (25+ min).
- Why polling stopped (not proven, spike step 1):
  a. Stalled request: `useSourceStatus` (frontend/src/lib/hooks/use-sources.ts:234) → `sourcesApi.status` (frontend/src/lib/api/sources.ts:80) uses apiClient timeout 600 000 ms (client.ts). TanStack v5 interval does not start new fetch while one in flight → one stalled poll (SSH tunnel hiccup) freezes card ≤10 min, then 3 retries each ≤10 min = up to 40 min.
  b. Hidden tab: `refetchIntervalInBackground` false → no polls while tab hidden (user in terminal). Resumes on visible, so explains pause, not "never".
  Fix covers both.
- Side findings (not in scope, give orchestrator for backlog):
  - title "tmpzadxwcsk.00088" (content-core temp file name for PDF URL) — should be PDF title or URL basename.
  - `GET /api/sources?notebook_id=...` says `embedded_chunks: 0` while GET one source says 24.
  - `GET /api/sources/{id}` bumps `source.updated` (03:12:54 → 03:14:39 on two plain GETs) → list sort by updated jumps on view.
  - GPU1 KFD list has 2 foreign PIDs (2436642 803 MB, 3655406 346 MB) besides vLLM 3318196 (63.3 GB).

## Speed options (decided)
| option | gain | cost | pick |
|---|---|---|---|
| EMBED_THREADS 16→32 | F0b spike 15 s→9 s per 16 texts (1.67x) → 24 chunks ~33 s | CPU only, box load 2.5/128 | YES |
| GPU embed (ROCm torch) | ~10x embed | free VRAM ~0.8 GiB now. Need ~2.5-3 GiB → LLM_GPU_UTIL 0.92→≤0.87, KV 7.48→~4.3 GiB (204k→~117k tok, concurrency 3.1x→1.8x @65k), +ROCm torch in venv-embed (~4 GB disk), foreign PIDs on GPU1 | NO (Gemma budget) |
| ONNX/OpenVINO int8 CPU | maybe 1.5-2x (Zen3 = AVX2, no VNNI/AVX512) | new deps, vector drift → rebuild all embeddings | NO now; only if check 5 fails |
| bigger chunks | ~0 (same tokens) | worse retrieval | NO |
| smaller model | 2-4x | new dim → rebuild all, quality drop | NO |
| WORKER_MAX_TASKS 2 (embed overlaps LLM) | ~-45 s | breaks local-llm rule WORKER_MAX_TASKS=1 | NO |
| parallel encodes | 0 (same cores) | thread storm (F0b) | NO |
After YES: this paper ≈ 49 + 33 + 7 ≈ 90 s.

## Steps
1. **Spike (15 min, executioner, playwright, scratch script not committed).** New scratch notebook, add link `https://arxiv.org/pdf/2610.00088` with Dense Summary transformation + embed on, watch card + network:
   a. tab visible whole time → does card finish? (expect yes)
   b. `page.route('**/sources/*/status', ...)` hold 1 request unanswered from t=10 s → card stuck? (expect stuck = cause a)
   c. visibility hidden 90 s (`Object.defineProperty(document,'visibilityState',{value:'hidden',configurable:true}); document.dispatchEvent(new Event('visibilitychange'))`), then visible → finishes?
   Write 1 line result to LOG.md. Delete scratch source + notebook after (own rows only).
2. `frontend/src/lib/api/sources.ts` `status`: `apiClient.get(..., { timeout: 15000 })` → stalled poll fails fast, retry fires.
3. `frontend/src/lib/hooks/use-sources.ts` `useSourceStatus`: add `refetchIntervalInBackground: true`, `refetchOnWindowFocus: true`; `retry`: keep 404 no-retry, else `failureCount < 3` with `retryDelay: 2000` (fixed, short). Interval fn: keep 2000 when last data status new/queued/running (query keeps last data on error → polling continues).
4. `frontend/src/components/sources/SourceCard.tsx` completion effect (~line 162): on completed/failed also `queryClient.invalidateQueries({queryKey: ['sources']})` besides `onRefresh` (list carries status; avoid list still 'running' after card done). No other logic change.
5. Test `frontend/src/lib/hooks/use-sources.test.tsx` (new, vitest + QueryClientProvider, fake timers): (i) `sourcesApi.status` mock running, running, completed with `document.visibilityState='hidden'` → called 3x, stops after completed; (ii) assert `apiClient.get` called with `{timeout: 15000}` (mock apiClient). 
6. `ops/env.sh`: `EMBED_THREADS` default 32; update comment (spike numbers + "F0d: 24-chunk paper 55.6 s @16"). Keep MICRO_BATCH 4, HIGH_BURST 3.
7. `.claude/skills/stack/NOTES.md`: 1 line "Embed EMBED_THREADS=32 (F0d); GPU embed rejected: needs LLM_GPU_UTIL ≤0.87".
8. CHANGELOG `## [Unreleased]` → `### Fixed` (existing section): "Source cards no longer stay stuck on processing when a status check stalls or the tab was in the background."
9. Restart embed + web (`ops/stack.sh restart embed web`, skill `stack`). Re-run spike 1b/1c → card finishes.

## Deps
None new.

## VRAM / RAM
GPU: 0 change (vLLM stays ~63.3 GB on GPU1). Embed CPU: 32 threads, RSS ~1.6 GB now (same model) — no change expected. Extra CPU load only during embed jobs.

## Checks (tester)
1. No stuck jobs: SurrealQL read `SELECT id,status FROM command WHERE status IN ['new','running']` → [] when idle; `GET /api/sources/source:ac4isz34prb6ftb9pak3/status` → `completed`.
2. UI normal: new notebook, Add Source → link `https://arxiv.org/pdf/2610.00088`, transformation Dense Summary, embed on → card spinner → done by itself ≤ 3 min, no reload; open source → Details "Embedded", Insights (1).
3. UI stall: same flow, playwright holds first `/status` request after 10 s forever → card still done ≤ 30 s after worker.log "Successfully embedded source"; network shows new status request ≤ 20 s after the held one.
4. UI hidden tab: same flow, emulate hidden right after add for 120 s → status requests keep coming while hidden; on visible card already done.
5. Speed: worker.log for check-2 source: "Created 24 chunks" → "Successfully embedded source ... 24 chunks in X s", X ≤ 40 (was 55.6). process_source + embed total ≤ 120 s.
6. `curl -s 127.0.0.1:8002/health` → `"threads":32`. `uv run --with pytest-playwright --with httpx pytest e2e/test_b4_b6_verify.py -k b6 -q --browser-channel chrome` → pass (no starvation regression).
7. Search: `POST /api/search {"query":"golden ratio extreme Kerr-Newman black hole","type":"vector","limit":5,"search_sources":true,"search_notes":false}` → hit with check-2 source id.
8. GPU: `ops/stack.sh gpu` → only GPU1 busy, vLLM pid VRAM unchanged (~63.3 GB), no new KFD pid from embed.
9. `uv run ruff check . && uv run ruff format --check . && uv run python -m mypy .`; `cd frontend && npm run lint && npm run test && npm run build` → pass (incl. new use-sources test).
10. Cleanup: tester deletes own check sources/notebooks; source:ac4isz34prb6ftb9pak3 untouched.

## Risks
- Cause not proven until spike 1; if 1a also stalls with no held request → look at SourceCard `enabled`/remount (shouldFetchStatus) before step 4; report to orchestrator.
- 15 s timeout on status: endpoint does 2 DB reads (~ms); fine. Too short only if API blocked → then retry, harmless.
- Background polling = 1 tiny GET / 2 s per processing card while hidden; stops at done.
- 32 threads on shared 2-socket box (other apps: pdf-rag, nomic vLLM) → if load high, drop to 24. NUMA cross-socket may cap gain below 1.67x.
- Still ~90 s for this paper: Gemma transformation (48 s) is user-chosen, not embed.
