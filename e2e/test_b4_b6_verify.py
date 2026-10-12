"""B4/B5/B6 verify (F0b). Break tests around the fixes:

- B4: worker restart fails only orphaned 'running' embed jobs; queued 'new' jobs
  survive and run; worker still works after the startup cleanup; API restart
  does not touch jobs; UI unlocks the source.
- B5: parallel POST /api/embed (mixed async modes, two sources) -> one job each.
- B6: vector search latency while an 88-chunk embed job runs; small requests
  must not starve a big one.

Slow (~10 min): B6 re-embeds the HRV source. Run alone:
  uv run --with pytest-playwright --with httpx pytest e2e/test_b4_b6_verify.py -q -s --browser-channel chrome
"""

import concurrent.futures as cf
import subprocess
import threading
import time

import httpx
import pytest
from conftest import EMBED, ROOT, WEB, active_embed_jobs, sql
from playwright.sync_api import Page, expect

HRV_SOURCE = "source:v4e42282fh9zk3gfhxum"
FACT = "The zorblat sensor samples at 47 hertz on the left wrist."
WORKER_MSG = "Worker restarted while this job was running"
Q = "heart rate variability egocentric video"


def _status(api: httpx.Client, cmd: str) -> dict:
    return api.get(f"/api/commands/jobs/{cmd}").json()


def _wait_status(api, cmd, want: tuple[str, ...], timeout: float) -> dict:
    end = time.time() + timeout
    st: dict = {}
    while time.time() < end:
        st = _status(api, cmd)
        if st.get("status") in want:
            return st
        time.sleep(2)
    pytest.fail(f"job {cmd} not in {want} after {timeout}s: {st}")


def _stack(cmd: str, svc: str):
    r = subprocess.run(
        ["bash", "ops/stack.sh", cmd, svc], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr


def _wait_api(timeout: float = 120):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get("http://127.0.0.1:5055/health", timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    pytest.fail("api not healthy after restart")


def _post(api, sid: str, async_processing: bool = False) -> httpx.Response:
    return api.post(
        "/api/embed",
        json={
            "item_id": sid,
            "item_type": "source",
            "async_processing": async_processing,
        },
    )


def _close_own(cmds):
    for c in cmds:
        st = sql(f"SELECT VALUE status FROM {c};")[0]
        if st and st[0] in ("new", "running"):
            sql(f"UPDATE {c} SET status = 'failed', error_message = 'tester cleanup';")


@pytest.fixture
def text_source(api):
    ids = []

    def make(title: str) -> str:
        r = api.post(
            "/api/sources",
            data={
                "type": "text",
                "content": FACT * 3,
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


# ---------------------------------------------------------------- B4


def test_b4_2_restart_fails_running_keeps_queued(
    page: Page, api, text_source, evidence_dir
):
    a = text_source(f"b4-a-{int(time.time())}")
    b = text_source(f"b4-b-{int(time.time())}")
    _stack("down", "embed")
    own = []
    log = []
    try:
        ca = _post(api, a).json()["command_id"]
        own.append(ca)
        _wait_status(api, ca, ("running",), 60)
        cb = _post(api, b).json()["command_id"]  # max-tasks 1 -> stays 'new'
        own.append(cb)
        time.sleep(3)
        log.append(
            f"before restart: A {_status(api, ca)['status']} B {_status(api, cb)['status']}"
        )
        assert _status(api, cb)["status"] == "new"

        _stack("restart", "worker")
        time.sleep(8)
        sa, sb = _status(api, ca), _status(api, cb)
        log.append(
            f"after restart: A {sa['status']} err={sa.get('error_message')!r} B {sb['status']}"
        )
        assert sa["status"] == "failed", sa
        assert WORKER_MSG in (sa.get("error_message") or ""), sa
        assert sb["status"] in ("new", "running"), sb  # queued job not killed

        # source A unlocked: API + UI
        src = api.get(f"/api/sources/{a}").json()
        assert src["embed_command_id"] is None, src
        page.goto(WEB, wait_until="networkidle")  # B2: fresh deep link -> /notebooks
        page.goto(f"{WEB}/sources/{a}", wait_until="networkidle")
        page.get_by_role("tab", name="Details").click()
        btn = page.get_by_role("button", name="Embed Content")
        expect(btn).to_be_visible(timeout=15000)
        expect(btn).to_be_enabled()
        expect(page.get_by_role("button", name="Embedding...")).to_have_count(0)
        page.screenshot(
            path=str(evidence_dir / "B4-verify-unlocked.png"), full_page=True
        )

        _stack("up", "embed")
        # worker DB still alive after startup cleanup: B runs to completion
        sb = _wait_status(api, cb, ("completed", "failed", "canceled"), 300)
        log.append(f"B final: {sb['status']}")
        assert sb["status"] == "completed", sb
        # A re-embed gets a new job and completes
        r = _post(api, a).json()
        ca2 = r["command_id"]
        own.append(ca2)
        assert ca2 != ca, r
        sa2 = _wait_status(api, ca2, ("completed", "failed", "canceled"), 300)
        log.append(f"A re-embed {ca2}: {sa2['status']}")
        assert sa2["status"] == "completed", sa2
        assert api.get(f"/api/sources/{a}").json()["embedded"] is True
    finally:
        (evidence_dir / "B4-verify.txt").write_text("\n".join(log) + "\n")
        _stack("up", "embed")
        _close_own(own)


def test_b4_3_api_restart_does_not_touch_running_job(api, text_source):
    sid = text_source(f"b4-api-{int(time.time())}")
    _stack("down", "embed")
    cmd = None
    try:
        cmd = _post(api, sid).json()["command_id"]
        _wait_status(api, cmd, ("running",), 60)
        _stack("restart", "api")
        _wait_api()
        st = _status(api, cmd)
        assert st["status"] == "running", st  # API import of commands must not fail it
        assert api.get(f"/api/sources/{sid}").json()["embed_command_id"] == cmd
        _stack("up", "embed")
        st = _wait_status(api, cmd, ("completed", "failed", "canceled"), 300)
        assert st["status"] == "completed", st
    finally:
        _stack("up", "embed")
        if cmd:
            _close_own([cmd])


# ---------------------------------------------------------------- B5


@pytest.mark.parametrize("rnd", range(3))
def test_b5_2_parallel_mixed_modes_two_sources(api, text_source, rnd):
    s1 = text_source(f"b5-1-{rnd}")
    s2 = text_source(f"b5-2-{rnd}")
    _stack("down", "embed")
    try:
        args = [
            (s, m) for s in (s1, s2) for m in (False, True, False, True, False, True)
        ]
        with cf.ThreadPoolExecutor(len(args)) as ex:
            rs = list(ex.map(lambda x: _post(api, *x), args))
        assert all(r.status_code == 200 for r in rs), [r.text for r in rs]
        for s in (s1, s2):
            cmds = {r.json()["command_id"] for (sid, _), r in zip(args, rs) if sid == s}
            jobs = sql(
                "SELECT id, status FROM command WHERE name='embed_source' "
                f"AND args.source_id = '{s}';"
            )[0]
            assert len(cmds) == 1 and len(jobs) == 1, (s, cmds, jobs)
    finally:
        _stack("up", "embed")
        for s in (s1, s2):
            for j in active_embed_jobs(s):
                _wait_status(
                    api, str(j["id"]), ("completed", "failed", "canceled"), 600
                )


# ---------------------------------------------------------------- B6


def _search_time(api) -> tuple[float, bool]:
    t = time.time()
    r = api.post(
        "/api/search",
        json={
            "query": Q,
            "type": "vector",
            "limit": 10,
            "search_sources": True,
            "search_notes": False,
        },
        timeout=300,
    )
    dt = time.time() - t
    assert r.status_code == 200, r.text
    hit = any(
        HRV_SOURCE in str(x.get("parent_id") or x.get("id"))
        for x in r.json()["results"]
    )
    return dt, hit


def test_b6_search_latency_during_embed(api, evidence_dir):
    idle = [_search_time(api)[0] for _ in range(2)]
    r = _post(api, HRV_SOURCE)
    cmd = r.json()["command_id"]
    _wait_status(api, cmd, ("running",), 120)
    time.sleep(20)
    during = []
    for _ in range(5):
        if _status(api, cmd)["status"] != "running":
            break
        during.append(_search_time(api))
        time.sleep(4)
    t0 = time.time()
    st = _wait_status(api, cmd, ("completed", "failed", "canceled"), 600)
    after = [_search_time(api)[0] for _ in range(2)]
    (evidence_dir / "B6-verify.txt").write_text(
        f"job {cmd} {st['status']} (waited {time.time() - t0:.0f}s more after searches)\n"
        f"idle s: {[round(x, 2) for x in idle]}\n"
        f"during s/hit: {[(round(d, 2), h) for d, h in during]}\n"
        f"after s: {[round(x, 2) for x in after]}\n"
        f"before fix during: 25.5, 78.9, 56.1\n"
    )
    assert st["status"] == "completed", st
    assert len(during) >= 3, during
    assert all(h for _, h in during), during
    assert max(d for d, _ in during) < 15, during  # was 25-79 s


def test_b6_small_requests_do_not_starve_big(evidence_dir):
    """Break: 4 clients spam 1-text embeds; a 16-text request must still finish."""
    texts = [("heart rate variability sensor wearable " * 40)[:1100]] * 16
    stop = threading.Event()
    small = []

    def spam():
        while not stop.is_set():
            t = time.time()
            httpx.post(
                f"{EMBED}/v1/embeddings", json={"input": "query heart"}, timeout=300
            )
            small.append(time.time() - t)

    ths = [threading.Thread(target=spam, daemon=True) for _ in range(4)]
    for th in ths:
        th.start()
    time.sleep(1)
    t = time.time()
    try:
        r = httpx.post(f"{EMBED}/v1/embeddings", json={"input": texts}, timeout=300)
        big = time.time() - t
    finally:
        stop.set()
        for th in ths:
            th.join(timeout=60)
    # baseline alone
    t = time.time()
    httpx.post(f"{EMBED}/v1/embeddings", json={"input": texts}, timeout=300)
    alone = time.time() - t
    (evidence_dir / "B6-verify-starvation.txt").write_text(
        f"16-text alone {alone:.1f}s, under 4x small spam {big:.1f}s; "
        f"small n={len(small)} max {max(small):.2f}s\n"
    )
    assert r.status_code == 200
    assert big < max(60, alone * 3), (big, alone)
