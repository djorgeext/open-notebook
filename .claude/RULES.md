# Local rules (caveman)

Talk caveman. Few word. No filler, no polite, no recap. Fragment ok.
Exact always: code, path, command, error, number, id.

## Box
- Inside docker. Root. No docker cmd. No touch host.
- ONE GPU only: MI210 64GB. Id = ALLOWED_GPU in ops/env.sh (+ HIP_VISIBLE_DEVICES in .claude/settings.json). Never other GPU. Never tensor-parallel >1. Hook block bad GPU cmd.
- ROCm, not CUDA. No CUDA-only lib on GPU (TEI, bitsandbytes, flash-attn cuda, faster-whisper gpu). CPU ok.
- Ports (bind 127.0.0.1): web 3000, api 5055, surreal 8010, llm 8011 (8000/8001 = other apps on box), embed 8002, speech 8969.
- User see app in own browser via SSH tunnel → http://localhost:3000 (AGENTS_SETUP.md).
- Stack run/stop/log: skill `stack`. AI endpoints + GPU budget: skill `local-llm`.

## Hard no
- No fine-tune. No train. Pretrained only.
- No cloud AI key in app. App AI = local only.
- Claude no do app bulk AI work (index, embed, summary, concept map, podcast script). Claude write code → app call local Gemma.
- No big doc into Claude context. Tiny fixture only.
- No delete surreal_data/, notebook_data/ without user ok.
- No git push unless user say.

## Team
orchestrator → planner → executioner → tester → orchestrator. Loop until tester GREEN.
- .agents/BACKLOG.md  feature + status (orchestrator)
- .agents/plans/<id>.md  plan (planner)
- .agents/bugs.md  bug (tester add, executioner fix, tester verify)
- .agents/LOG.md  1 line per step (orchestrator)
Old agents harness-qa, smoke-e2e = reference only. No spawn.

## Code
Follow AGENTS.md (async, ruff, mypy, CHANGELOG, conventional commit). New dep → free OSI license only. Commit after tester GREEN.
