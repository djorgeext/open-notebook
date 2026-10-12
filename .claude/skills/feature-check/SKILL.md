---
name: feature-check
description: Tester checklist of all app features, bug format, e2e runner, evidence rules. Use when verify features or fixes.
---
# Feature check (caveman)

Base: web http://127.0.0.1:3000, api http://127.0.0.1:5055/api (docs /docs).
E2E run: `uv run --with pytest-playwright --with httpx pytest e2e -q --browser-channel chrome`
API recipe: .claude/agents/smoke-e2e.md (read only part need).
Fixture: e2e/fixtures/ tiny (txt ~1 KB, pdf 1-2 page, mp3 ≤10 s, known facts inside). Make if missing.
Async job: poll command status, timeout 5 min (local LLM slow).
Judge AI answer by fixture fact (regex/contains), not vibe. Flaky → run 2x, mark flaky.

## Core (every run)
- C1 stack: all health ok; `/api/models/defaults` = local models
- C2 notebook: create, rename, delete
- C3 source: text, url, pdf, audio → processed + embedded (youtube if net)
- C4 search: vector + text hit fixture fact
- C5 chat: answer has fixture fact + citation; click citation open source
- C6 ask: search + synthesis answer ok
- C7 transformation → insight/note made
- C8 note: create, edit, delete
- C9 podcast short → audio file, duration > 0
- C10 settings/models page load, show local models
- C11 UI: main pages no console error; screenshot each

## Feature (tester add 1 line after GREEN)
- F0 stack+tunnel: C1 + tunnel per AGENTS_SETUP.md (3000+5055, alt 13000) load models page; 3000-only shows "Unable to Connect" — e2e/test_c1_stack.py, e2e/test_c1_ui.py, e2e/test_b1_tunnel.py
- F0b embed: embed svc 16x1100ch < 60 s, threads < 200 under parallel load; Embed Content button "Embedding..." disabled → done toast, alert gone, "Already Embedded"; embed svc down → error toast names exception, button back; real arxiv link (https://arxiv.org/pdf/2608.18711) add via UI → embed → 88 chunks + vector hit — e2e/test_f0b_embed.py, e2e/test_f0b_embed_ui.py (fixture e2e/fixtures/make_tiny_pdf.py). Slow (~15 min total).
- F0b-prio embed svc bounded priority (B6): vector search < 15 s + hit during 88-chunk job; 16-text < max(60 s, 3x alone) under 4x 1-text spam; 2x16-text bounded under 8x spam — e2e/test_b4_b6_verify.py::test_b6_*, e2e/test_b6_break.py (B7/B8 xfail strict). ~10 min.
- F0d status poll: arxiv 2610.00088 link (Dense Summary + embed) from notebook page -> card done by itself <=15 s after process done, no reload; held status poll -> new poll <=20 s, card done; tab hidden 120 s -> polls continue, done on return; 503 storm 55 s -> recovers; Details "Embedded", Insights (1), 24 chunks, embed <=40 s; embed /health threads 32 — e2e/test_f0d_status_poll_ui.py (~10 min). Side B9/B10/B11 xfail strict e2e/test_f0d_side.py.

## Bug format (append .agents/bugs.md)
```
B<n> | <feature id> | sev:high|med|low | open
  repro: steps
  expect: ...
  got: ... (exact error line)
  evidence: .agents/evidence/<file>
```
Status: open → fixed (executioner) → verified (tester) | reopen.
