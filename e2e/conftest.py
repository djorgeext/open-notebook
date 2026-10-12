"""Shared e2e config. Run: uv run --with pytest-playwright --with httpx pytest e2e -q --browser-channel chrome"""

import os
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / ".agents" / "evidence"

WEB = os.environ.get("E2E_WEB", "http://127.0.0.1:3000")
API = os.environ.get("E2E_API", "http://127.0.0.1:5055")
LLM = os.environ.get("E2E_LLM", "http://127.0.0.1:8011")
EMBED = os.environ.get("E2E_EMBED", "http://127.0.0.1:8002")
SPEECH = os.environ.get("E2E_SPEECH", "http://127.0.0.1:8969")
SURREAL = os.environ.get("E2E_SURREAL", "http://127.0.0.1:8010")

TTS_MODEL = "speaches-ai/Kokoro-82M-v1.0-ONNX"
STT_MODEL = "Systran/faster-whisper-small"


@pytest.fixture(scope="session")
def api() -> Iterator[httpx.Client]:
    headers = {}
    pw = os.environ.get("OPEN_NOTEBOOK_PASSWORD")
    if pw:
        headers["Authorization"] = f"Bearer {pw}"
    with httpx.Client(base_url=API, headers=headers, timeout=300) as c:
        yield c


def _env(key: str, default: str = "") -> str:
    for ln in (ROOT / ".env").read_text().splitlines():
        if ln.startswith(f"{key}="):
            return ln.split("=", 1)[1].strip().strip("'\"")
    return os.environ.get(key, default)


def sql(query: str) -> list:
    """Run SurrealQL over HTTP (read-mostly; tester only touches its own test rows)."""
    r = httpx.post(
        f"{SURREAL}/sql",
        content=query,
        auth=(_env("SURREAL_USER", "root"), _env("SURREAL_PASSWORD")),
        headers={
            "surreal-ns": _env("SURREAL_NAMESPACE", "open_notebook"),
            "surreal-db": _env("SURREAL_DATABASE", "open_notebook"),
            "Accept": "application/json",
        },
        timeout=60,
    )
    r.raise_for_status()
    out = r.json()
    for stmt in out:
        assert stmt["status"] == "OK", stmt
    return [stmt["result"] for stmt in out]


def active_embed_jobs(source_id: str) -> list[dict]:
    return sql(
        "SELECT id, status FROM command WHERE name = 'embed_source' "
        f"AND args.source_id = '{source_id}' AND status IN ['new', 'running'];"
    )[0]


@pytest.fixture(scope="session")
def evidence_dir() -> Path:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    return EVIDENCE
