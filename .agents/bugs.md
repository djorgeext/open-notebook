# Bugs (tester add, executioner fix, tester verify). Format: skill feature-check.

B1 | F0 | sev:med | verified
  cause: doc said only 3000 needed; /config auto-detect gives browser <host>:5055 direct. AGENTS_SETUP.md now says forward both 3000 + 5055 (13000 alt keeps 5055).
  repro: AGENTS_SETUP.md:71 says "Only port 3000 is required; the UI proxies `/api` to the backend." Tunnel only `-L 3000:127.0.0.1:3000` (sim: playwright abort all browser req to :5055), open http://localhost:3000/settings/models.
  expect: UI loads data via 3000 only (as doc claims).
  got: "Unable to Connect to API Server". `GET /config` -> `{"apiUrl":"http://127.0.0.1:5055"}` (Host-derived `<host>:5055`, frontend/src/app/config/route.ts auto-detect) -> browser calls :5055 direct, never the /api rewrite. Full tunnel cmd (both -L) works; only the sentence is false.
  fix hint: doc only — drop "Only port 3000 is required…", say both 3000 + 5055 must be forwarded (and 13000 alt still needs 5055 free locally). No app code change.
  evidence: .agents/evidence/B1-config.txt, .agents/evidence/B1-only3000-unable-connect.png; test e2e/test_c1_ui.py::test_f0_b1_tunnel_doc_matches_api_path
  verify 2026-10-11: doc no "Only port 3000"; tunnel cmd + ssh config + 13000 alt all forward 5055. /config Host localhost:3000|13000 -> http://localhost:5055; CORS *. UI: both ports -> models page ok; 5055 blocked -> "Unable to Connect" (as doc warns); 13000->3000 fwd + 5055 -> ok. 2 runs pass. evidence: .agents/evidence/B1-verify-{both-ports,only3000,alt13000}.png; test e2e/test_b1_tunnel.py

B2 | C11 (upstream, not F0) | sev:low | open
  repro: fresh browser (no localStorage), no OPEN_NOTEBOOK_PASSWORD. Open http://127.0.0.1:3000/settings/models directly.
  expect: land on /settings/models.
  got: NAV /settings/models -> /login -> /notebooks. frontend/src/app/(dashboard)/layout.tsx:33 stores sessionStorage `redirectAfterLogin`, but frontend/src/components/auth/LoginForm.tsx:49 (no-auth path) does `router.push('/notebooks')` ignoring it. Second visit (auth-storage set) works.
  evidence: .agents/evidence/B2-deeplink.png; test e2e/test_c1_ui.py::test_b2_fresh_deep_link_models (xfail)

B3 | F0b | sev:med | verified
  cause: page only knew job while open (no job state in GET source), /api/embed always queued new job, embed_source DELETEd chunks before generating. Fix: Source.get_active_embed_command(); vectorize()+/api/embed return active job; GET /api/sources/{id} embed_command_id → UI resumes poll, button "Embedding..." disabled; old chunks deleted only after new inserted (insert fail → old kept).
  repro: source embedded (source:v4e42282fh9zk3gfhxum). Click Embed Content (or POST /api/embed) → job running. Reload page (or leave + come back) while job runs → Details tab.
  expect: page knows embed job running: button "Embedding..." disabled (or alert says in progress); no second job possible.
  got: "Content Not Embedded" alert + "Embed Content" button enabled (job command:twjrqxx0qcq1kxenhb6u status running, source embedded:false embedded_chunks:0). Click again → 2nd embed_source job queued (WORKER_MAX_TASKS=1 → queue blocked ~5 min per dup) = original F0b dup-job symptom, only guarded while page stays open. Also: embed_source DELETEs old chunks first (commands/embedding_commands.py "DELETE source_embedding" before generate) → re-embed of an embedded source makes it unsearchable for whole job (~5 min) and forever if job fails.
  fix hint: frontend: on load, if source.command_id / latest embed job for source is new|running → resume waitForCommand + keep button disabled. API: /api/embed reject (409) or return existing command_id if an embed_source job for item is new|running. Backend (upstream): delete old chunks after new ones generated, not before.
  evidence: .agents/evidence/B3-reload-midjob.png
  verify 2026-10-12: orphan command:ux69krw335flr0u6q3sd status failed ("stale"). UI (copy of HRV text, 88 chunks): dblclick Embed Content -> 1 job; reload / leave+return / 2nd tab -> "Embedding..." disabled, menu item disabled, no "Embed Content"; POST /api/embed mid-job -> same command_id; resumed poll -> "Content embedded" toast, 88 chunks, embed_command_id null. Re-embed HRV (API): embedded_chunks never < 88, vector search hit every sample, new chunk ids disjoint from old, final 88. F0b regression subset 10/10 pass. New issues found: B4 (high), B5 (low), B6 (low).
  evidence: .agents/evidence/B3-verify-{reload,return,tab2,done}.png; tests e2e/test_b3_embed_dedupe.py (b3_1, b3_3), e2e/test_b3_embed_dedupe_ui.py

B4 | F0b (regression from B3 fix) | sev:high | verified
  cause: surreal-commands worker never reclaims 'running' jobs after restart -> dead job counted as active forever. Fix: worker start (commands/worker_startup.py, via `--import-modules commands`) marks embed_source 'running' jobs failed ("Worker restarted while this job was running. Embed again.") before taking jobs; API process skips it (argv check).
  repro: text source, embed off. ops/stack.sh down embed (keeps job retrying). POST /api/embed -> job running. ops/stack.sh restart worker (SIGTERM, = normal restart after code change; also crash/OOM). ops/stack.sh up embed. Wait 90 s. GET job, GET source, POST /api/embed again.
  expect: dead job closed (failed / re-queued) or new embed allowed; source usable.
  got: job command:2hmldghvr6bopyobh7gl stays "running" forever (surreal-commands worker only picks status='new' on start, surreal_commands/core/worker.py:111; never reclaims 'running'). GET /api/sources/{id} embed_command_id = dead id -> UI "Embedding..." disabled forever; re-POST /api/embed returns the dead id ("Source embedding job submitted", command_id command:2hmldghvr6bopyobh7gl) -> no new job. Source can never be embedded from UI/API; cancel endpoint is a stub (api/command_service.py), only manual DB edit escapes. Before fix user could at least click again.
  why high: permanent lockout per source, no user escape, trigger = any worker restart mid-embed (embed job ~5 min, restarts routine).
  fix hint: don't trust 'running' blindly. Options: (a) worker start (or API lifespan) marks embed_source 'running' jobs failed ("worker restarted") since WORKER_MAX_TASKS jobs in flight die with the worker; (b) get_active_embed_command ignores jobs older than a cap (command.created/updated are null in DB -> use result.execution_metadata.started_at or add own timestamp); (c) real cancel endpoint + UI "cancel" when stuck. Test: e2e/test_b3_embed_dedupe.py::test_b3_4_worker_restart_mid_job_unlocks_source (xfail strict -> remove marker when fixed). Tester closed own orphan by UPDATE status failed.
  evidence: .agents/evidence/B4-worker-restart.txt
  verify 2026-10-12: e2e/test_b4_b6_verify.py::test_b4_2 pass (A running + B queued, restart worker -> A failed "Worker restarted while this job was running. Embed again.", B kept -> completed; source A embed_command_id null, UI "Embed Content" enabled, no "Embedding..."; re-embed A new job completed). test_b4_3 pass (API restart leaves running job, job completes). test_b3_4 pass (no xfail). Evidence .agents/evidence/B4-verify.txt, B4-verify-unlocked.png.
  note (not blocking): cleanup only embed_source; other commands (process_source, podcast) left 'running' by dead worker still stay running forever (pre-existing). Assumes one worker process: 2nd worker start would fail live jobs of 1st.

B5 | F0b | sev:low | verified
  cause: check-then-submit not atomic. Fix: Source.submit_embed_job() = per-loop asyncio.Lock around check+submit, used by vectorize() + /api/embed async path; rebuild_embeddings skips sources with active embed job.
  repro: text source, embed svc down (keeps job alive). 8 parallel POST /api/embed {item_id, item_type:"source"} (async_processing false and true both).
  expect: 1 embed_source job, all 8 responses same command_id.
  got: 5/10 runs >1 job, e.g. {command:dqbvgep1gjpuaol9gwdm running, command:hbtvat7eaudiltp9sbko new}; one run >=4 jobs. Check-then-submit not atomic (Source.vectorize + api/routers/embedding.py async path: get_active_embed_command() then submit_command()). UI dblclick ok (1 job) — only parallel clients (2 tabs, scripts). Also rebuild_embeddings path (commands/embedding_commands.py:716) submits embed_source without the check. Impact: duplicate ~5 min job blocks queue (WORKER_MAX_TASKS=1); no data damage.
  fix hint: per-source asyncio.Lock in API process around check+submit (single api process), or unique DB guard; rebuild skip sources with active job.
  evidence: .agents/evidence/B5-parallel-embed-race.txt; test e2e/test_b3_embed_dedupe.py::test_b3_2_parallel_posts_one_job (xfail non-strict)
  verify 2026-10-12: test_b5_2 x3 rounds (12 parallel POST, mixed async false/true, 2 sources) -> 1 job + 1 command_id per source, 3/3 pass. test_b3_2[False,True] (8 parallel, no xfail) 2/2 pass. 5/5 runs clean (was 5/10 dup).

B6 | F0b (embed svc lock) | sev:low | verified
  cause: embed server one plain lock per whole 16-text request. Fix: ops/embed_server.py PriorityLock + micro-batches (EMBED_MICRO_BATCH=4); <=2-text requests jump queue. Measured mid-job search 3.9-9.9 s (was 25-79 s), idle 0.56 s, 88-chunk job 230 s.
  repro: POST /api/embed HRV source (88 chunks). 20 s later time POST /api/search {"query":"heart rate variability egocentric video","type":"vector",...} x3.
  expect: search ~1 s (idle 0.6 s).
  got: 25.5 s, 78.9 s, 56.1 s while job runs; 0.61 s after. ops/embed_server.py global lock -> query embedding waits behind 16-text batch encodes. Same for chat/ask retrieval during any embed job (risk of frontend/http timeouts).
  fix hint: query-priority (small requests jump queue), smaller job batch, or separate lock/thread budget for 1-text requests.
  evidence: .agents/evidence/B6-search-latency-during-embed.txt
  verify 2026-10-12: latency part PASS (test_b6_search_latency_during_embed): idle 0.88/0.61 s, during job 8.5/4.78/5.45/8.3/11.77 s all hit HRV (was 25-79 s), after 0.63/0.7 s, 88-chunk job completed. Evidence .agents/evidence/B6-verify.txt.
  reopen: fix starves big requests. test_b6_small_requests_do_not_starve_big FAIL: `httpx.ReadTimeout: timed out` (e2e/test_b4_b6_verify.py:273, 16-text POST, timeout 300 s) while 4 threads loop 1-text POST /v1/embeddings.
    repro (scratchpad starve.py, same as test): 16 x 1100ch alone 14.2 s; with 1 small-query client 14.2 s ok; with 2 clients TIMEOUT >120 s (small n=800, max 0.40 s); with 4 clients TIMEOUT >120 s.
    cause: ops/embed_server.py PriorityLock.hold: low waits `while self._busy or (not high and self._waiting_high)` -> strict priority; with >=2 concurrent small callers there is always a high waiter, low (job micro-batch) never gets lock. Embed job blocked as long as query traffic continues (2 tabs chat/search, ask fan-out, scripts) -> app embed client timeout -> job retry/fail.
    expect: 16-text request finishes in bounded time (< max(60 s, 3x alone)) under continuous small traffic; small still faster than before fix.
    fix hint: bounded priority — after N (e.g. 2-4) consecutive high grants, give next turn to a waiting low; or FIFO ticket with high allowed to skip at most one low micro-batch; or aging (low waiting > X s becomes high).
  evidence: .agents/evidence/B6-starvation-repro.txt; test e2e/test_b4_b6_verify.py::test_b6_small_requests_do_not_starve_big
  fix round 3: strict priority starved low. PriorityLock now bounded: after EMBED_HIGH_BURST (3) high grants in a row, waiting low slice gets next turn (ops/embed_server.py, ops/env.sh). Executioner run: starve test 16-text alone 14.0 s, under 4x spam 14.9 s (small max 7.7 s); latency test during 8.4/5.2/5.4/7.8/10.6 s all hit, job completed. Both test_b6_* pass.
  verify round 3 2026-10-12: PASS. embed svc pid started 02:11:31 after ops/embed_server.py edit 02:11:26 (env EMBED_HIGH_BURST=3, MICRO_BATCH=4). test_b6_small_requests_do_not_starve_big PASS: 16-text alone 14.0 s, under 4x spam 14.9 s. test_b6_search_latency_during_embed PASS: idle 1.11/0.63 s, during 8.39/5.06/5.52/7.9/10.66 s all hit HRV, after 0.64/0.71 s, job command:pnluqu8ubiv5w8a0i4ds completed, HRV 88 chunks. Break e2e/test_b6_break.py::test_b6_break_two_big_under_spam PASS (2x16-text alone 28.5 s, under 8x spam 33.3/24.8 s). Smoke 24/24 (test_c1_stack, test_f0b_2/2b/2c/7, UI test_f0b_5 + test_f0b_6_8). Side effects split out: B7, B8.
  evidence: .agents/evidence/B6-verify.txt, B6-verify-starvation.txt, B6-break-two-big.txt

B7 | F0b (embed svc lock, side effect of B6 fix) | sev:low | open
  cause: ops/embed_server.py PriorityLock is not FIFO among high (<=2-text) callers. Since round 3, highs must wait during a low turn, so several pile up. Then notify_all + fresh arrivals (which check _may_enter without waiting) race, and one small caller can lose round after round.
  repro: 4 threads loop POST /v1/embeddings {"input":"query heart"}; meanwhile one 48 x 1100ch request (~45 s). Record per-client latency (scratch tail.py = e2e/test_b6_break.py::test_b7_small_callers_fair_during_big).
  expect: each small waits <= ~2 rounds (HIGH_BURST smalls + one 4-text slice ~3.8 s) -> < 10 s; clients served about equally.
  got: 1 client max 3.74 s, 2 clients max 3.86 s (ok). 4 clients: client 3 n=3 waits 8.87/16.04/24.87 s while others n=12-15; rerun client 2 n=5 max 15.21 s. 8 clients + 2 bigs: small p95 23-25 s, max 25-29 s (B6 pre-fix search 25-79 s). Starve test evidence: 4 clients small max 15.23 s = whole 16-text duration.
  why low: needs >=3-4 concurrent query callers during an embed job (single-user app; ask fan-out / multi-tab). Bounded in practice by job length, not starvation forever, but no hard bound.
  fix hint: FIFO ticket for highs (deque of waiter tokens, grant head), and fresh arrivals queue behind existing waiters (no barging when _waiting_* > 0).
  evidence: .agents/evidence/B7-small-unfair.txt, B7-verify.txt; test e2e/test_b6_break.py::test_b7_small_callers_fair_during_big (xfail strict -> remove marker when fixed)

B8 | F0b (embed svc) | sev:low | open
  cause: ops/embed_server.py endpoints are sync `def` -> run in anyio threadpool (default 40). /health and /v1/models share pool with embed calls blocked in PriorityLock.hold -> pool full -> health waits.
  repro: 48 threads loop 1-/2-text POST /v1/embeddings + one 16-text request; time GET /health + GET /v1/models every 2 s.
  expect: health+models < 5 s (ops/stack.sh status uses `curl -sf -m 5`).
  got: first probe 19.65 s (rerun 19.3 s), later 2.3-4.4 s; small p50 10.4 s max 51 s. stack.sh status would show embed HEALTH fail under load.
  why low: 48 concurrent embed clients is unrealistic for single-user app; no data loss.
  fix hint: make /health and /v1/models `async def` (no pool thread), or run encode via own bounded executor / raise anyio limiter.
  evidence: .agents/evidence/B8-verify.txt; test e2e/test_b6_break.py::test_b8_health_answers_under_pool_saturation (xfail strict)

B9 | F0d (side, pre-existing upstream) | sev:low | open
  repro: POST /api/sources link https://arxiv.org/pdf/2610.00088 embed false sync; SELECT updated; GET /api/sources/{id}; SELECT updated.
  expect: plain GET does not change source.updated (list sort by updated stable).
  got: updated 03:51:02.357 -> 03:51:05.314 after one GET. api/routers/sources.py:105 _stamp_source_view `UPDATE $source_id SET last_viewed_at = time::now();` + migrations/1.surrealql:14 `DEFINE FIELD updated ON source ... VALUE time::now()` -> every view rewrites updated. source:ac4isz34prb6ftb9pak3 updated 03:14:41 -> 03:31:06 from one tester GET.
  fix hint: stamp via separate table/relation, or make updated VALUE keep $before.updated when only last_viewed_at changes.
  evidence: .agents/evidence/B9-B10-repro.txt; test e2e/test_f0d_side.py::test_b9_get_source_does_not_bump_updated (xfail strict)

B10 | F0d (side) | sev:low | open
  repro: POST /api/sources type=link url=https://arxiv.org/pdf/2610.00088 (no title).
  expect: title = PDF title or URL basename ("2610.00088").
  got: title 'tmpjzvnm5do.00088' (content-core temp file name); same for user source:ac4isz34prb6ftb9pak3 'tmpzadxwcsk.00088'. Search results show "Dense Summary - tmpzadxwcsk.00088".
  evidence: .agents/evidence/B9-B10-repro.txt; test e2e/test_f0d_side.py::test_b10_pdf_link_title_not_temp_name (xfail strict)

B11 | F0d (side, delete during jobs) | sev:med | open
  repro: POST /api/sources link arxiv 2610.00088, embed true, async, transformations [Dense Summary]. Poll /status until completed (~58 s; embed_source running, embed_insight new). DELETE /api/sources/{id} -> 200. Wait jobs done.
  expect: no source_embedding / source_insight rows for deleted source; jobs cancel or skip deleted source.
  got: jobs all `completed`; `SELECT count() FROM source_embedding WHERE source=source:bvx44v7lr8kacswm849t` -> 24, source_insight 1 (source row gone). Ghost insights rank TOP in vector search: POST /api/search "golden ratio extreme Kerr-Newman black hole" -> top 3 = source_insight:* parent_id None title "Dense Summary - " (sim 0.76/0.75/0.75) above the real source. User likely hits this exactly in F0d case: card "stuck" -> user deletes it.
  fix hint: embed_source/create_insight/embed_insight re-check source exists before writing (and after encode, before insert), drop rows if gone; delete_source also cancels/marks active jobs and deletes chunks/insights after they finish; search filter rows whose source missing.
  note: 3 older 1-chunk orphans (source:ik5kuw2fmesbhwm2sj0k, lyjy18r72ljwkgej32vi, pc0t8cesbj0e3gcqc7ys) not tester rows, left in DB. Tester own orphans deleted.
  evidence: .agents/evidence/B11-delete-midjob-orphans.txt; test e2e/test_f0d_side.py::test_b11_delete_during_jobs_leaves_no_orphans (xfail strict, ~90 s)
