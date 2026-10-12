#!/usr/bin/env python3
"""Point Open Notebook at the local AI servers (idempotent, stdlib only).

Creates one `openai_compatible` credential per local server, registers the
models, and sets every default model to a local one. Run via `ops/stack.sh wire`
(after `ops/stack.sh up`; that sources ops/env.sh so ports are right).
Env: API (default http://127.0.0.1:5055/api), OPEN_NOTEBOOK_PASSWORD (if auth on),
plus the model names from ops/env.sh.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("API", "http://127.0.0.1:5055/api")
PASSWORD = os.environ.get("OPEN_NOTEBOOK_PASSWORD", "")
E = os.environ.get
LLM = E("LLM_NAME", "local-llm")
EMB = E("EMBED_NAME", "local-embed")
TTS = E("TTS_MODEL", "speaches-ai/Kokoro-82M-v1.0-ONNX")
STT = E("STT_MODEL", "Systran/faster-whisper-small")

SERVERS = [
    # credential name, base_url, modalities, [(model, type)]
    (
        "local-vllm",
        f"http://127.0.0.1:{E('LLM_PORT', '8011')}/v1",
        ["language"],
        [(LLM, "language")],
    ),
    (
        "local-embed",
        f"http://127.0.0.1:{E('EMBED_PORT', '8002')}/v1",
        ["embedding"],
        [(EMB, "embedding")],
    ),
    (
        "local-speaches",
        f"http://127.0.0.1:{E('SPEECH_PORT', '8969')}/v1",
        ["text_to_speech", "speech_to_text"],
        [(TTS, "text_to_speech"), (STT, "speech_to_text")],
    ),
]
DEFAULTS = {
    "default_chat_model": LLM,
    "default_transformation_model": LLM,
    "large_context_model": LLM,
    "default_tools_model": LLM,
    "default_embedding_model": EMB,
    "default_text_to_speech_model": TTS,
    "default_speech_to_text_model": STT,
}


def call(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Content-Type", "application/json")
    if PASSWORD:
        req.add_header("Authorization", f"Bearer {PASSWORD}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}: {e.read().decode()[:400]}")


def main() -> None:
    creds = {c["name"]: c for c in call("GET", "/credentials")}
    for name, base_url, modalities, models in SERVERS:
        cred = creds.get(name)
        if not cred:
            cred = call(
                "POST",
                "/credentials",
                {
                    "name": name,
                    "provider": "openai_compatible",
                    "modalities": modalities,
                    "base_url": base_url,
                    "api_key": "local",
                },
            )
            print(f"credential created: {name}")
        res = call(
            "POST",
            f"/credentials/{cred['id']}/register-models",
            {
                "models": [
                    {"name": m, "provider": "openai_compatible", "model_type": t}
                    for m, t in models
                ]
            },
        )
        print(f"{name}: models created={res['created']} existing={res['existing']}")

    by_key = {(m["name"], m["type"]): m["id"] for m in call("GET", "/models")}
    type_of = {
        LLM: "language",
        EMB: "embedding",
        TTS: "text_to_speech",
        STT: "speech_to_text",
    }
    payload = {}
    for field, name in DEFAULTS.items():
        mid = by_key.get((name, type_of[name]))
        if not mid:
            sys.exit(f"model not found after register: {name}")
        payload[field] = mid
    call("PUT", "/models/defaults", payload)
    print("defaults set:", json.dumps(call("GET", "/models/defaults"), indent=1))


if __name__ == "__main__":
    main()
