#!/usr/bin/env python3
"""PreToolUse guard for this box.

Keeps every command on the ONE allowed GPU, blocks GPU-state changes,
multi-GPU parallelism, deletion of data dirs, and edits to guard config.
Exit code 2 blocks the tool call; stderr is shown to Claude.
"""

import json
import os
import re
import sys

ALLOWED = os.environ.get("ALLOWED_GPU", "1").strip()
OK_VALUES = {ALLOWED, "$ALLOWED_GPU", "${ALLOWED_GPU}", '"$ALLOWED_GPU"'}
PROTECTED_FILES = (".claude/settings.json", ".claude/hooks/")

VIS_RE = re.compile(
    r"\b(HIP_VISIBLE_DEVICES|CUDA_VISIBLE_DEVICES|ROCR_VISIBLE_DEVICES|GPU_DEVICE_ORDINAL)"
    r"\s*=\s*['\"]?([^\s'\";&|)]*)"
)
PAR_RE = re.compile(
    r"(--(?:tensor|pipeline|data)-parallel-size|\s-tp|\s-pp|\s-dp)[=\s]+(\d+)"
)


def block(msg: str) -> None:
    print(
        f"BLOCKED by gpu_guard: {msg}. Allowed GPU = {ALLOWED} only.", file=sys.stderr
    )
    sys.exit(2)


def check_visibility(text: str) -> None:
    for var, val in VIS_RE.findall(text):
        if var != "HIP_VISIBLE_DEVICES":
            block(f"{var} not allowed (use HIP_VISIBLE_DEVICES={ALLOWED})")
        if val not in OK_VALUES:
            block(f"HIP_VISIBLE_DEVICES={val}")
    for val in re.findall(r"\bALLOWED_GPU\s*=\s*['\"]?([^\s'\";&|)]*)", text):
        if val not in {ALLOWED, f"${{ALLOWED_GPU:-{ALLOWED}}}"}:
            block(f"changing ALLOWED_GPU to {val}")


def check_bash(cmd: str) -> None:
    check_visibility(cmd)
    if re.search(r"\bunset\b[^;&|\n]*\bHIP_VISIBLE_DEVICES\b", cmd) or re.search(
        r"\benv\b[^;&|\n]*(-u\s*HIP_VISIBLE_DEVICES|\s-i\b|--ignore-environment)", cmd
    ):
        block("removing HIP_VISIBLE_DEVICES exposes all GPUs")
    for flag, n in PAR_RE.findall(cmd):
        if int(n) > 1:
            block(f"{flag.strip()} {n} (single GPU box)")
    if re.search(r"\brocm-smi\b[^;&|\n]*(--set|--reset|--gpureset|\s-r\b)", cmd):
        block("rocm-smi state change (shared machine)")
    if re.search(r"\b(amd-smi)\b[^;&|\n]*\b(set|reset)\b", cmd):
        block("amd-smi state change (shared machine)")
    if re.search(r"\brm\b[^;&|\n]*\b(surreal_data|notebook_data)\b", cmd):
        block("deleting data dir needs user ok")


def check_edit(tool_input: dict) -> None:
    path = str(tool_input.get("file_path", ""))
    if any(p in path for p in PROTECTED_FILES):
        block(f"{path} is user-only config")
    chunks = [tool_input.get("content", ""), tool_input.get("new_string", "")]
    chunks += [e.get("new_string", "") for e in tool_input.get("edits", []) or []]
    for text in chunks:
        if text:
            check_visibility(str(text))


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    if tool == "Bash":
        check_bash(str(tool_input.get("command", "")))
    elif tool in ("Write", "Edit", "MultiEdit"):
        check_edit(tool_input)
    sys.exit(0)


if __name__ == "__main__":
    main()
