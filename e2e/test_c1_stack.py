"""C1 stack + F0 checks (API side). Plan: .agents/plans/F0.md checks 1-12."""

import json
import re
import socket
import struct
import subprocess

import httpx
import pytest
from conftest import API, EMBED, LLM, ROOT, SPEECH, STT_MODEL, SURREAL, TTS_MODEL, WEB

OUR_PORTS = {3000, 5055, 8002, 8010, 8011, 8969}


def _listeners() -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        for line in open(f).readlines()[1:]:
            p = line.split()
            if p[3] != "0A":  # LISTEN
                continue
            ip, port_hex = p[1].split(":")
            port = int(port_hex, 16)
            if len(ip) == 8:
                addr = socket.inet_ntoa(struct.pack("<I", int(ip, 16)))
            else:
                b = bytes.fromhex(ip)
                addr = socket.inet_ntop(
                    socket.AF_INET6,
                    b"".join(b[i : i + 4][::-1] for i in range(0, 16, 4)),
                )
            out.setdefault(port, set()).add(addr)
    return out


def test_f0_1_stack_status():
    r = subprocess.run(
        ["ops/stack.sh", "status"], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    rows = {}
    for line in r.stdout.splitlines()[1:]:
        p = line.split()
        if len(p) >= 2 and p[0] in {
            "surreal",
            "llm",
            "embed",
            "speech",
            "api",
            "worker",
            "web",
        }:
            rows[p[0]] = p[1:]
    assert len(rows) == 7, r.stdout
    for svc, cols in rows.items():
        assert cols[0] == "running", f"{svc}: {cols}"
        if svc != "worker":
            assert cols[1] == "ok", f"{svc}: {cols}"


@pytest.mark.parametrize(
    "url",
    [
        f"{SURREAL}/health",
        f"{LLM}/v1/models",
        f"{EMBED}/v1/models",
        f"{SPEECH}/health",
        f"{API}/health",
        WEB,
    ],
)
def test_c1_health(url):
    assert httpx.get(url, timeout=30, follow_redirects=True).status_code == 200


def test_f0_2_ports_loopback_only():
    lis = _listeners()
    for port in OUR_PORTS:
        assert port in lis, f"{port} not listening"
        assert lis[port] <= {"127.0.0.1", "::1"}, f"{port} bound {lis[port]}"
    # foreign apps untouched
    assert 8000 in lis and 8001 in lis
    ids = [
        m["id"]
        for m in httpx.get("http://127.0.0.1:8001/v1/models", timeout=30).json()["data"]
    ]
    assert "nomic-ai/nomic-embed-text-v1.5" in ids


def test_f0_3_llm_model():
    d = httpx.get(f"{LLM}/v1/models", timeout=30).json()["data"]
    assert d[0]["id"] == "local-llm"
    assert d[0]["root"] in {"google/gemma-4-26B-A4B-it", "google/gemma-4-E4B-it"}


def _chat(payload: dict) -> str:
    r = httpx.post(f"{LLM}/v1/chat/completions", json=payload, timeout=300)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def test_f0_4_llm_pong():
    c = _chat(
        {
            "model": "local-llm",
            "messages": [{"role": "user", "content": "Reply with exactly: PONG"}],
            "max_tokens": 20,
        }
    )
    assert "PONG" in c


def test_f0_5_llm_json_schema():
    c = _chat(
        {
            "model": "local-llm",
            "messages": [{"role": "user", "content": "give a=7"}],
            "max_tokens": 100,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "t",
                    "schema": {
                        "type": "object",
                        "properties": {"a": {"type": "integer"}},
                        "required": ["a"],
                    },
                },
            },
        }
    )
    assert json.loads(c)["a"] == 7


def _allowed_gpu() -> int:
    r = subprocess.run(
        ["bash", "-c", 'source ops/env.sh && printf %s "$ALLOWED_GPU"'],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return int(r.stdout.strip())


def test_f0_6_gpu_only_allowed():
    allowed = _allowed_gpu()
    out = subprocess.run(
        ["rocm-smi", "--showmeminfo", "vram"], capture_output=True, text=True
    ).stdout
    used = {
        int(m.group(1)): int(m.group(2))
        for m in re.finditer(
            r"GPU\[(\d+)\]\s*: VRAM Total Used Memory \(B\): (\d+)", out
        )
    }
    assert used[allowed] >= 50e9, used
    for gpu, b in used.items():
        if gpu != allowed:
            # baseline (other tenants) recorded 2026-10-11: ~1.16 GB
            assert b <= 2.2e9, f"GPU {gpu} used {b}"


def test_f0_7_embed_dim():
    r = httpx.post(
        f"{EMBED}/v1/embeddings",
        json={"model": "local-embed", "input": ["hello"]},
        timeout=60,
    )
    assert len(r.json()["data"][0]["embedding"]) == 1024


def test_f0_8_tts_stt_roundtrip(tmp_path):
    r = httpx.post(
        f"{SPEECH}/v1/audio/speech",
        json={"model": TTS_MODEL, "voice": "af_heart", "input": "hello world"},
        timeout=120,
    )
    assert r.status_code == 200
    assert len(r.content) > 1024
    t = httpx.post(
        f"{SPEECH}/v1/audio/transcriptions",
        files={"file": ("t.mp3", r.content, "audio/mpeg")},
        data={"model": STT_MODEL},
        timeout=120,
    )
    assert "hello" in t.json()["text"].lower()


EXPECT_NAMES = {
    "default_chat_model": "local-llm",
    "default_transformation_model": "local-llm",
    "large_context_model": "local-llm",
    "default_tools_model": "local-llm",
    "default_embedding_model": "local-embed",
    "default_text_to_speech_model": TTS_MODEL,
    "default_speech_to_text_model": STT_MODEL,
}


def test_c1_f0_9_defaults_local(api):
    defaults = api.get("/api/models/defaults").json()
    models = {m["id"]: m for m in api.get("/api/models").json()}
    for field, name in EXPECT_NAMES.items():
        mid = defaults.get(field)
        assert mid, f"{field} null"
        assert models[mid]["provider"] == "openai_compatible", field
        assert models[mid]["name"] == name, field
    creds = api.get("/api/credentials").json()
    for c in creds:
        assert c["base_url"].startswith("http://127.0.0.1:"), c["base_url"]


def test_f0_10_model_tests(api):
    defaults = api.get("/api/models/defaults").json()
    for field in (
        "default_chat_model",
        "default_embedding_model",
        "default_speech_to_text_model",
        "default_text_to_speech_model",
    ):
        r = api.post(f"/api/models/{defaults[field]}/test").json()
        assert r["success"] is True, (field, r)
        if field == "default_embedding_model":
            assert "1024" in r["message"]


def test_f0_11_wire_idempotent(api):
    before = api.get("/api/models/defaults").json()
    n_models = len(api.get("/api/models").json())
    r = subprocess.run(
        ["ops/stack.sh", "wire"], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in r.stdout.splitlines() if "created=" in ln]
    assert lines and all("created=0" in ln for ln in lines), r.stdout
    assert api.get("/api/models/defaults").json() == before
    assert len(api.get("/api/models").json()) == n_models


def test_f0_12_worker():
    r = subprocess.run(
        ["pgrep", "-f", "bin/surreal-commands-worker"], capture_output=True, text=True
    )
    assert len(r.stdout.split()) == 1, r.stdout
    log = (ROOT / ".run" / "worker.log").read_text()
    assert "Starting Surreal Commands worker" in log
    assert "Traceback" not in log
