"""B3 verify (F0b): one embed job per source, re-embed keeps old chunks searchable,
orphan job closed, worker death mid-job. API side. UI side: e2e/test_b3_embed_dedupe_ui.py.

Slow: test_b3_3 re-embeds the 88-chunk HRV source (~5 min on CPU).
"""

import concurrent.futures as cf
import subprocess
import time

import httpx
import pytest
from conftest import ROOT, active_embed_jobs, sql

HRV_SOURCE = "source:v4e42282fh9zk3gfhxum"
ORPHAN_JOB = "command:ux69krw335flr0u6q3sd"
FACT = "The zorblat sensor samples at 47 hertz on the left wrist."


def _poll(api: httpx.Client, cmd: str, timeout: float) -> dict:
    end = time.time() + timeout
    st: dict = {}
    while time.time() < end:
        st = api.get(f"/api/commands/jobs/{cmd}").json()
        if st.get("status") in ("completed", "failed", "canceled"):
            return st
        time.sleep(3)
    pytest.fail(f"job {cmd} not done in {timeout}s: {st}")


def _stack(cmd: str, svc: str):
    r = subprocess.run(
        ["bash", "ops/stack.sh", cmd, svc], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr


def _hit(api: httpx.Client, query: str, sid: str) -> bool:
    r = api.post(
        "/api/search",
        json={
            "query": query,
            "type": "vector",
            "limit": 10,
            "search_sources": True,
            "search_notes": False,
        },
    )
    assert r.status_code == 200, r.text
    return any(
        sid in str(x.get("parent_id") or x.get("id")) for x in r.json()["results"]
    )


def _chunk_ids(sid: str) -> set[str]:
    return {
        str(x)
        for x in sql(f"SELECT VALUE id FROM source_embedding WHERE source = {sid};")[0]
    }


@pytest.fixture
def text_source(api):
    ids = []

    def make(title: str, content: str = FACT * 3) -> str:
        r = api.post(
            "/api/sources",
            data={
                "type": "text",
                "content": content,
                "title": title,
                "embed": "false",
                "async_processing": "false",
            },
        )
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
        return r.json()["id"]

    yield make
    for i in ids:
        api.delete(f"/api/sources/{i}")


def test_b3_1_orphan_job_closed(api):
    st = api.get(f"/api/commands/jobs/{ORPHAN_JOB}").json()
    assert st.get("status") == "failed", st


@pytest.mark.parametrize("async_processing", [False, True])
def test_b3_2_parallel_posts_one_job(api, text_source, async_processing):
    """Break: 8 simultaneous POST /api/embed (double click x4) -> exactly one job."""
    sid = text_source(f"b3-par-{async_processing}")
    _stack("down", "embed")  # keep the job alive (retrying) while we look
    try:

        def post(_):
            return api.post(
                "/api/embed",
                json={
                    "item_id": sid,
                    "item_type": "source",
                    "async_processing": async_processing,
                },
            )

        with cf.ThreadPoolExecutor(8) as ex:
            rs = list(ex.map(post, range(8)))
        assert all(r.status_code == 200 for r in rs), [r.text for r in rs]
        cmds = {r.json()["command_id"] for r in rs}
        jobs = active_embed_jobs(sid)
        all_jobs = sql(
            f"SELECT id, status FROM command WHERE name='embed_source' "
            f"AND args.source_id = '{sid}';"
        )[0]
        assert len(all_jobs) == 1 and len(cmds) == 1, (cmds, all_jobs, jobs)
        # sequential retry also returns the same id
        r = post(0)
        assert r.json()["command_id"] in cmds
        assert api.get(f"/api/sources/{sid}").json()["embed_command_id"] in cmds
    finally:
        _stack("up", "embed")
        for j in active_embed_jobs(sid):
            _poll(api, str(j["id"]), 600)


def test_b3_3_reembed_keeps_old_chunks_searchable(api):
    before = _chunk_ids(HRV_SOURCE)
    assert len(before) >= 80, len(before)
    q = "heart rate variability egocentric video"
    assert _hit(api, q, HRV_SOURCE)

    r = api.post("/api/embed", json={"item_id": HRV_SOURCE, "item_type": "source"})
    assert r.status_code == 200, r.text
    cmd = r.json()["command_id"]
    r2 = api.post("/api/embed", json={"item_id": HRV_SOURCE, "item_type": "source"})
    assert r2.json()["command_id"] == cmd, (cmd, r2.json())

    samples = []
    end = time.time() + 600
    st: dict = {}
    while time.time() < end:
        st = api.get(f"/api/commands/jobs/{cmd}").json()
        src = api.get(f"/api/sources/{HRV_SOURCE}").json()
        if st.get("status") in ("completed", "failed", "canceled"):
            break
        hit = _hit(api, q, HRV_SOURCE)
        samples.append(
            (st.get("status"), src["embedded_chunks"], hit, src["embed_command_id"])
        )
        time.sleep(5)
    assert st.get("status") == "completed", st
    assert (st.get("result") or {}).get("success") is not False, st
    running = [s for s in samples if s[0] == "running"]
    assert running, samples
    assert all(s[1] >= 80 for s in samples), samples  # never dropped to 0
    assert all(s[2] for s in samples), samples  # search hit the whole time
    assert all(s[3] == cmd for s in samples), samples  # page can see the job

    after = _chunk_ids(HRV_SOURCE)
    src = api.get(f"/api/sources/{HRV_SOURCE}").json()
    assert src["embed_command_id"] is None, src["embed_command_id"]
    assert 80 <= len(after) <= 96 and src["embedded_chunks"] == len(after), len(after)
    assert not (before & after), "old chunks not replaced"
    assert _hit(api, q, HRV_SOURCE)
    print(f"samples={len(samples)} max_chunks={max(s[1] for s in samples)}")


def test_b3_4_worker_restart_mid_job_unlocks_source(api, text_source, evidence_dir):
    """Break: worker restarted (ops/stack.sh restart worker = SIGTERM) while job runs."""
    sid = text_source("b3-worker-death")
    _stack("down", "embed")
    cmd = None
    try:
        r = api.post("/api/embed", json={"item_id": sid, "item_type": "source"})
        cmd = r.json()["command_id"]
        end = time.time() + 60
        while time.time() < end:
            if api.get(f"/api/commands/jobs/{cmd}").json()["status"] == "running":
                break
            time.sleep(1)
        _stack("restart", "worker")
        _stack("up", "embed")
        time.sleep(90)  # worker back, embed back: give it every chance
        st = api.get(f"/api/commands/jobs/{cmd}").json()
        src = api.get(f"/api/sources/{sid}").json()
        r2 = api.post("/api/embed", json={"item_id": sid, "item_type": "source"})
        (evidence_dir / "B4-worker-restart.txt").write_text(
            f"job {cmd}: {st}\nsource embed_command_id: {src['embed_command_id']}\n"
            f"embedded: {src['embedded']}\nre-POST /api/embed: {r2.json()}\n"
        )
        # expect: job closed (failed/re-queued) or a new embed possible
        assert st["status"] != "running" or r2.json()["command_id"] != cmd, st
    finally:
        _stack("up", "embed")
        if cmd and api.get(f"/api/commands/jobs/{cmd}").json()["status"] == "running":
            # tester-owned orphan: close it so it can't block anything
            sql(
                f"UPDATE {cmd} SET status = 'failed', "
                "error_message = 'tester: orphan after worker restart (B4)';"
            )
