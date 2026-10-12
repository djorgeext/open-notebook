# Backlog (orchestrator own). Status: todo / doing / done / blocked
# Line: id | goal | status
#   check: how tester know done

F0 | stack local up: surreal, llm (Gemma 26B, 1 GPU), embed CPU, speech CPU, api, worker, web; models wired | done
  check: C1; user see UI at http://localhost:3000 via tunnel
F0b | user bug: add source via link to PDF → "Embed Content" button does nothing | done
  check: add PDF URL source, click Embed Content, embedding job runs, source shows embedded, search finds it
F0d | user bug: arxiv 2610.00088 embed/process >20 min, too slow. Find where time goes (extract, chunk count, embed CPU), speed up | done
  check: same paper fully processed + embedded in few min; search hits; no GPU OOM for Gemma
F0e | B11 med: delete source mid-job leaves orphan chunks + ghost insights ranking top in vector search; clean 3 old orphans only with user ok. Low: B9 GET bumps updated, B10 PDF link title tmp*.00088 | todo
  check: e2e/test_f0d_side.py B9-B11 xfail markers removed and pass
F0c | embed_server polish: B7 small callers FIFO, B8 async /health (see bugs.md) | todo
  check: e2e/test_b6_break.py B7+B8 xfail markers removed and pass
F1 | baseline: tester run C1-C11, log bugs, fix until GREEN | todo
  check: C1-C11 GREEN
F2 | hybrid search: BM25 (SurrealDB full-text) + vector, RRF fuse, CPU reranker (Qwen3-Reranker-0.6B) | todo
  check: exact-term query + paraphrase query both hit fixture; recall better than vector-only on 20 Q
F3 | contextual chunk prefix at index time by local Gemma (worker job, cached doc prompt) | todo
  check: chunks store prefix; search hit rate same or better on golden Q
F4 | span citations: quote-first + chunk ids + fuzzy quote verify + CPU NLI check; chip hover = quote, click = highlight in source/PDF | todo
  check: every cited quote found verbatim in source; unsupported sentence flagged
F5 | concept map per notebook by local Gemma (worker job) → markmap UI, node click → chat about node | todo
  check: map renders, nodes carry source ids, click opens chat with topic
F6 | study tools: briefing, study guide, FAQ, timeline, flashcards + quiz with FSRS — JSON schema, cited | todo
  check: each artifact made from fixture, cites sources, quiz scoring works
F7 | data tables: extract tables across sources, cell → citation, export CSV/XLSX | todo
  check: table from fixture pdf matches values, export opens
F8 | grounded code analysis: chat writes Python on notebook tables, run sandboxed (no docker/KVM → spike: Pyodide vs restricted subprocess), chart + provenance | todo
  check: "chart column X" makes chart with source caption; sandbox no network
F9 | podcast formats deep dive / brief / critique / debate, multi-language, local TTS | todo
  check: each format makes audio, script cites sources
F10 | slides PPTX + slide-video (TTS + ffmpeg) | todo
  check: pptx opens, mp4 plays, slides cite sources
F11 | interactive audio: user interrupt hosts, answer from notebook, resume | todo
  check: question mid-play → spoken grounded answer
F12 | deep research: SearXNG + crawl → candidate sources, user approve before add | todo
  check: query → list of sources, approved ones ingested

# Out (1 GPU): image/video gen models together with Gemma. Only time-swap if ever.
