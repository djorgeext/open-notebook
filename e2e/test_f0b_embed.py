"""F0b: Embed Content (API + ops side). Plan: .agents/plans/F0b.md checks 1-4, 7 + break tests.

Slow: test_f0b_4 re-embeds the 88-chunk arxiv source (~5 min on CPU).
"""

import concurrent.futures as cf
import random
import subprocess
import threading
import time

import httpx
import pytest
from conftest import EMBED, ROOT

HRV_SOURCE = "source:v4e42282fh9zk3gfhxum"
STALE_JOBS = [
    "command:ioe9cemxmckwyhbhpvtg",
    "command:nshmkdl9gowz1voxxgsm",
    "command:35mzjeq10sa4n2kev2xd",
    "command:rvybng55ysf341jqv434",
]
# Orphan: was 'running' with no worker; closed as failed (B3 verify).
ORPHAN_JOB = "command:ux69krw335flr0u6q3sd"
THREAD_LIMIT = 200
WORDS = (
    "heart rate variability egocentric video sensor signal model attention "
    "camera wearable dataset"
).split()


def _texts(n: int, seed: int = 1) -> list[str]:
    rnd = random.Random(seed)
    return [" ".join(rnd.choice(WORDS) for _ in range(200))[:1100] for _ in range(n)]


def _embed_nlwp() -> int:
    pid = (ROOT / ".run" / "embed.pid").read_text().strip()
    return int(open(f"/proc/{pid}/status").read().split("Threads:")[1].split()[0])


def _poll(api: httpx.Client, cmd: str, timeout: float) -> dict:
    end = time.time() + timeout
    st: dict = {}
    while time.time() < end:
        st = api.get(f"/api/commands/jobs/{cmd}").json()
        if st.get("status") in ("completed", "failed", "canceled"):
            return st
        time.sleep(5)
    pytest.fail(f"job {cmd} not done in {timeout}s: {st}")


def test_f0b_2_embed_16x1100_fast():
    t = time.time()
    r = httpx.post(
        f"{EMBED}/v1/embeddings",
        json={"input": _texts(16), "model": "local-embed"},
        timeout=300,
    )
    dt = time.time() - t
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert len(data) == 16
    assert {len(d["embedding"]) for d in data} == {1024}
    assert dt < 60, f"16x1100 took {dt:.1f}s"


def test_f0b_2b_concurrent_requests_no_thread_storm():
    """Break: 4 parallel batches (old meltdown trigger). Threads stay bounded, all 200."""
    peak = [_embed_nlwp()]
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            peak.append(_embed_nlwp())
            time.sleep(0.5)

    th = threading.Thread(target=sample, daemon=True)
    th.start()

    def call(i):
        return httpx.post(
            f"{EMBED}/v1/embeddings",
            json={"input": _texts(8, seed=i), "model": "local-embed"},
            timeout=300,
        ).status_code

    try:
        with cf.ThreadPoolExecutor(4) as ex:
            codes = list(ex.map(call, range(4)))
    finally:
        stop.set()
        th.join()
    assert codes == [200] * 4, codes
    assert max(peak) < THREAD_LIMIT, f"peak threads {max(peak)}"


def test_f0b_2c_bad_inputs():
    """Break: empty list -> 400 not 500; dimensions truncation keeps unit norm."""
    r = httpx.post(f"{EMBED}/v1/embeddings", json={"input": []}, timeout=60)
    assert r.status_code == 400, r.text
    r = httpx.post(
        f"{EMBED}/v1/embeddings", json={"input": "x", "dimensions": 64}, timeout=60
    )
    assert r.status_code == 200, r.text
    v = r.json()["data"][0]["embedding"]
    assert len(v) == 64
    assert abs(sum(x * x for x in v) - 1) < 1e-3


def test_f0b_3_stale_jobs_not_pending(api):
    bad = {}
    for cid in STALE_JOBS:
        st = api.get(f"/api/commands/jobs/{cid}").json().get("status")
        if st in ("new", "queued", "running"):
            bad[cid] = st
    assert not bad, bad


def test_f0b_3_orphan_job(api):  # closed (failed "stale") 2026-10-12
    st = api.get(f"/api/commands/jobs/{ORPHAN_JOB}").json().get("status")
    assert st not in ("new", "queued", "running"), st


def test_f0b_4_embed_hrv_source_and_threads(api):
    r = api.post("/api/embed", json={"item_id": HRV_SOURCE, "item_type": "source"})
    assert r.status_code == 200, r.text
    cmd = r.json().get("command_id")
    assert cmd, r.json()

    peak = [_embed_nlwp()]
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            peak.append(_embed_nlwp())
            time.sleep(2)

    th = threading.Thread(target=sample, daemon=True)
    th.start()
    t = time.time()
    try:
        st = _poll(api, cmd, 600)
    finally:
        stop.set()
        th.join()
    dt = time.time() - t
    assert st["status"] == "completed", st
    assert (st.get("result") or {}).get("success") is not False, st
    src = api.get(f"/api/sources/{HRV_SOURCE}").json()
    assert src["embedded"] is True, src.get("embedded")
    assert 80 <= src["embedded_chunks"] <= 96, src["embedded_chunks"]  # 88 +- chunker
    assert max(peak) < THREAD_LIMIT, f"peak threads {max(peak)}"
    print(
        f"embed job {cmd}: {dt:.0f}s, chunks {src['embedded_chunks']}, peak thr {max(peak)}"
    )


def test_f0b_7_vector_search_hits_hrv(api):
    r = api.post(
        "/api/search",
        json={
            "query": "heart rate variability egocentric video",
            "type": "vector",
            "limit": 5,
            "search_sources": True,
            "search_notes": False,
        },
    )
    assert r.status_code == 200, r.text
    ids = [x.get("parent_id") or x.get("id") for x in r.json()["results"]]
    assert any(HRV_SOURCE in str(i) for i in ids), ids


def test_f0b_break_embed_bad_targets(api):
    r = api.post(
        "/api/embed", json={"item_id": "source:doesnotexist", "item_type": "source"}
    )
    assert r.status_code == 404, (r.status_code, r.text)
    r = api.post("/api/embed", json={"item_id": HRV_SOURCE, "item_type": "banana"})
    assert r.status_code == 400, (r.status_code, r.text)


TIMEOUT_PROBE = """
import asyncio
from esperanto import AIFactory
from open_notebook.utils.embedding import _describe_error
m = AIFactory.create_embedding("openai-compatible", "local-embed",
    config={"base_url": "%s/v1", "api_key": "x"})
texts = ["heart rate variability egocentric video sensor " * 25] * 32
async def main():
    try:
        await m.aembed(texts)
        print("DESC: no timeout")
    except Exception as e:
        print("DESC:", _describe_error(e))
asyncio.run(main())
"""


def test_f0b_8_forced_read_timeout_named(evidence_dir):
    """Check 8 (timeout flavor): 1s client timeout -> error text names ReadTimeout, not blank."""
    import os

    env = {**os.environ, "ESPERANTO_EMBEDDING_TIMEOUT": "1"}
    r = subprocess.run(
        ["uv", "run", "python", "-c", TIMEOUT_PROBE % EMBED],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    desc = [ln for ln in r.stdout.splitlines() if ln.startswith("DESC:")]
    (evidence_dir / "F0b-T8-timeout.txt").write_text(
        "\n".join(desc) or r.stderr[-2000:]
    )
    assert desc, r.stderr[-2000:]
    assert "ReadTimeout" in desc[0] or "TimeoutError" in desc[0], desc[0]
