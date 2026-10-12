# Log (orchestrator). One line per step: date | item | who | result
2026-10-11 | F0 | executioner | stack up 7/7 ok, gemma-4-26B-A4B-it on GPU1 (63.2GB used, GPU0 1.16GB baseline unchanged), wire ok, 4 model tests ok
2026-10-11 | orch | F0 GREEN (1 bug round, B1 doc fixed). Ports surreal 8010, llm 8011. Not committed yet.
2026-10-11 | F0b | executioner | embed spike (16/50 texts x1100ch): thr8 26s/82s, thr16 15s/46s, thr32 9s/28s → EMBED_THREADS=16, batch 16, timeout 300. Real source 88 chunks ≈ 280-300s, nlwp 65-80.
2026-10-12 | orch | F0b GREEN (3 rounds: B3-B6 fixed; B7,B8 low → F0c). Not committed.
2026-10-12 | F0d | executioner | spike pre-fix: a visible → card done at api done (55 s, 26 polls); b hold 1 status @11.5 s → polls stop, card STUCK 120 s+ after api done (cause a proven); c hidden 150 s → 1 poll only, card done 2 s after visible
2026-10-12 | F0d | executioner | fix done: status timeout 15s + retryDelay 2s + bg polling + list invalidate; EMBED_THREADS 32. Re-spike b: held poll @11.7s → next poll 28.7s, card done 2s after api; c hidden whole run → 38 polls, card done while hidden. Embed 24 chunks 55.6s→36.3/35.2s. Scratch rows deleted.
2026-10-12 | orch | F0d GREEN (polling fix, embed 32 thr). B9-B11 → F0e. Not committed.
