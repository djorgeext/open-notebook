"""F0d UI: link source card goes processing -> done by itself (no reload).

Plan: .agents/plans/F0d.md checks 2, 3, 4, 5, 7 (+ tester break cases).
Real paper https://arxiv.org/pdf/2610.00088 (24 chunks), Dense Summary + embed on,
added from a scratch notebook page. Each case ~2 min (worker runs 1 job at a time).
Cleanup: own sources + notebook only.
"""

import json
import re
import subprocess
import time
from datetime import datetime

import pytest
from conftest import ROOT, WEB, sql
from playwright.sync_api import Page, Route, expect

ARXIV_URL = "https://arxiv.org/pdf/2610.00088"
WORKER_LOG = ROOT / ".run" / "worker.log"
IGNORE_CONSOLE = ("Download the React DevTools", "[Fast Refresh]", "[HMR]")
BUSY_WORDS = re.compile(r"Processing|Preparing|Queued|Checking\.\.\.")
STATUS_RE = re.compile(r"/api/sources/[^/]+/status")
LOG_TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)")

pytestmark = pytest.mark.skipif(
    subprocess.run(
        ["curl", "-sfI", "-m", "20", ARXIV_URL], capture_output=True
    ).returncode
    != 0,
    reason="no net to arxiv",
)


def _log_time(sid: str, needle: str) -> tuple[float, str] | None:
    """Epoch + line of first worker.log line with needle and sid (log is local naive time)."""
    with open(WORKER_LOG, errors="replace") as f:
        for ln in f:
            if sid in ln and needle in ln:
                m = LOG_TS.match(ln)
                if m:
                    t = datetime.strptime(m.group(1)[:26], "%Y-%m-%d %H:%M:%S.%f")
                    return t.timestamp(), ln.strip()
    return None


def _wait_log(sid: str, needle: str, timeout: float) -> tuple[float, str]:
    end = time.time() + timeout
    while time.time() < end:
        hit = _log_time(sid, needle)
        if hit:
            return hit
        time.sleep(2)
    raise AssertionError(f"no worker.log '{needle}' for {sid} in {timeout}s")


@pytest.fixture
def notebook(api):
    r = api.post("/api/notebooks", json={"name": f"f0d-check-{int(time.time())}"})
    assert r.status_code == 200, r.text
    nb = r.json()["id"]
    yield nb
    srcs = [
        s["id"]
        for s in api.get("/api/sources", params={"notebook_id": nb, "limit": 50}).json()
    ]
    # B11: deleting mid-job leaves orphan chunks/insights -> wait for jobs first
    end = time.time() + 300
    while srcs and time.time() < end and any(_active_jobs(s) for s in srcs):
        time.sleep(3)
    for s in srcs:
        api.delete(f"/api/sources/{s}")
    api.delete(f"/api/notebooks/{nb}")


def _active_jobs(sid: str) -> list:
    return sql(
        f"SELECT id FROM command WHERE args.source_id = '{sid}' "
        "AND status IN ['new', 'queued', 'running'];"
    )[0]


class Watch:
    """Records status requests, main-frame navigations and console errors."""

    def __init__(self, page: Page):
        self.status: list[tuple[float, str]] = []  # (t, "req"/"ok"/"fail")
        self.navs: list[tuple[float, str]] = []
        self.errs: list[str] = []
        page.on("request", self._req)
        page.on("requestfinished", lambda r: self._mark(r, "ok"))
        page.on("requestfailed", lambda r: self._mark(r, "fail"))
        page.on(
            "framenavigated",
            lambda f: (
                self.navs.append((time.time(), f.url)) if f == page.main_frame else None
            ),
        )
        page.on("console", self._console)
        page.on("pageerror", lambda e: self.errs.append(f"pageerror: {e}"))

    def _req(self, r) -> None:
        if STATUS_RE.search(r.url):
            self.status.append((time.time(), "req"))

    def _mark(self, r, kind: str) -> None:
        if STATUS_RE.search(r.url):
            self.status.append((time.time(), kind))

    def _console(self, m) -> None:
        if m.type == "error" and not any(s in m.text for s in IGNORE_CONSOLE):
            self.errs.append(m.text)

    def reloads(self, t_add: float) -> list[str]:
        """Main-frame navigations after Done was clicked (should be none)."""
        return [u for t, u in self.navs if t > t_add]

    def reqs(self, t0: float = 0, t1: float = 1e18) -> list[float]:
        return [t for t, k in self.status if k == "req" and t0 <= t <= t1]


def _dense_id(api) -> str:
    for t in api.get("/api/transformations").json():
        if t["title"] == "Dense Summary":
            return t["id"]
    raise AssertionError("no Dense Summary transformation")


def _add_link(page: Page, api, nb: str, title: str) -> float:
    """Notebook page > Add Source > link, Dense Summary only, embed on > Done. Returns t of Done."""
    dense = _dense_id(api)
    page.goto(WEB, wait_until="networkidle")
    page.goto(f"{WEB}/notebooks/{nb}", wait_until="networkidle")
    page.get_by_role("button", name="Add Source").first.click()
    page.get_by_role("menuitem", name="Add Source").click()
    page.get_by_role("textbox", name="URL(s) *").fill(ARXIV_URL)
    page.get_by_role("textbox", name="Title (Optional)").fill(title)
    page.get_by_role("button", name="Next", exact=True).click()
    page.get_by_role("button", name="Next", exact=True).click()
    dlg = page.locator("[role=dialog]")
    boxes = dlg.locator("[role=checkbox][id^='checkbox-transformation:']")
    expect(boxes.first).to_be_visible(timeout=15000)
    for i in range(boxes.count()):
        b = boxes.nth(i)
        want = b.get_attribute("id") == f"checkbox-{dense}"
        if (b.get_attribute("data-state") == "checked") != want:
            b.click()
    emb = dlg.locator("#enable-embedding")
    if emb.get_attribute("data-state") != "checked":
        emb.click()
    expect(dlg.locator(f"[id='checkbox-{dense}']")).to_have_attribute(
        "data-state", "checked"
    )
    expect(emb).to_have_attribute("data-state", "checked")
    expect(dlg.locator("[role=checkbox][data-state=checked]")).to_have_count(2)
    page.get_by_role("button", name="Done", exact=True).click()
    return time.time()


def _sid(api, nb: str, title: str, timeout: float = 60) -> str:
    end = time.time() + timeout
    while time.time() < end:
        rows = api.get("/api/sources", params={"notebook_id": nb, "limit": 50}).json()
        hit = [s for s in rows if s["title"] == title]
        if hit:
            return hit[0]["id"]
        time.sleep(1)
    raise AssertionError(f"source {title} not created")


def _card(page: Page, title: str):
    return page.locator("div.group.relative.cursor-pointer", has_text=title).first


def _card_done(page: Page, title: str) -> bool:
    c = _card(page, title)
    if c.count() == 0:
        return False
    return c.locator("svg.animate-spin").count() == 0 and not BUSY_WORDS.search(
        c.inner_text()
    )


def _wait_card_done(page: Page, title: str, timeout: float) -> float | None:
    end = time.time() + timeout
    while time.time() < end:
        if _card_done(page, title):
            return time.time()
        page.wait_for_timeout(500)
    return None


def _set_hidden(page: Page, hidden: bool) -> None:
    page.evaluate(
        """h => {
            Object.defineProperty(document, 'visibilityState', {value: h ? 'hidden' : 'visible', configurable: true});
            Object.defineProperty(document, 'hidden', {value: h, configurable: true});
            document.dispatchEvent(new Event('visibilitychange'));
        }""",
        hidden,
    )


def _timeline(evidence_dir, name: str, t_add: float, w: Watch, extra: dict) -> None:
    rel = [(round(t - t_add, 1), k) for t, k in w.status]
    (evidence_dir / f"F0d-{name}.txt").write_text(
        json.dumps(
            {**extra, "status_events_rel_s": rel, "navs_after_add": w.reloads(t_add)},
            indent=1,
        )
    )


def _finish_checks(page: Page, api, sid: str, title: str, evidence_dir, name: str):
    """After embed + insight: open card -> Details 'Embedded', Insights (1); search hit."""
    _wait_log(sid, "Successfully created insight", 120)
    _card(page, title).click()
    tab = page.get_by_role("tab", name=re.compile(r"Insights \(1\)"))
    expect(tab).to_be_visible(timeout=20000)
    page.get_by_role("tab", name="Details").click()
    expect(page.get_by_text("Embedded", exact=True).first).to_be_visible(timeout=20000)
    expect(page.get_by_text("Content Not Embedded")).to_have_count(0)
    page.screenshot(path=str(ROOT / evidence_dir / f"F0d-{name}-details.png"))
    src = api.get(f"/api/sources/{sid}").json()
    assert src["embedded"] is True and src["embedded_chunks"] == 24, (
        src["embedded"],
        src["embedded_chunks"],
    )


def _speed(sid: str) -> dict:
    proc = _log_time(sid, "Successfully processed source")
    emb = _log_time(sid, "Successfully embedded source")
    chunks = _log_time(sid, "Created ")
    assert proc and emb and chunks
    m = re.search(r"(\d+) chunks in ([\d.]+)s", emb[1])
    p = re.search(r"in ([\d.]+)s", proc[1])
    assert m and p, (emb[1], proc[1])
    return {
        "chunks": int(m.group(1)),
        "embed_s": float(m.group(2)),
        "process_s": float(p.group(1)),
        "created_chunks_line": chunks[1][-120:],
    }


def test_f0d_2_card_done_visible_tab(page: Page, api, notebook, evidence_dir):
    """Check 2 + 5 + 7: normal flow, card done by itself, speed, vector search hit."""
    w = Watch(page)
    title = f"f0d-visible-{int(time.time())}"
    t_add = _add_link(page, api, notebook, title)
    sid = _sid(api, notebook, title)
    expect(_card(page, title)).to_be_visible(timeout=15000)
    page.screenshot(path=str(evidence_dir / "F0d-2-processing.png"))
    t_done = _wait_card_done(page, title, 180)
    proc = _log_time(sid, "Successfully processed source")
    _timeline(
        evidence_dir,
        "2-visible",
        t_add,
        w,
        {
            "sid": sid,
            "card_done_s": t_done and round(t_done - t_add, 1),
            "processed_s": proc and round(proc[0] - t_add, 1),
        },
    )
    assert t_done, "card not done in 180 s without reload"
    page.screenshot(path=str(evidence_dir / "F0d-2-done.png"))
    assert proc and t_done - proc[0] <= 15, t_done - proc[0] if proc else None
    assert not w.reloads(t_add), w.reloads(t_add)  # no reload
    _finish_checks(page, api, sid, title, evidence_dir, "2")

    sp = _speed(sid)
    (evidence_dir / "F0d-5-speed.txt").write_text(json.dumps({"sid": sid, **sp}))
    assert sp["chunks"] == 24, sp
    assert sp["embed_s"] <= 40, sp
    assert sp["process_s"] + sp["embed_s"] <= 120, sp

    r = api.post(
        "/api/search",
        json={
            "query": "golden ratio extreme Kerr-Newman black hole",
            "type": "vector",
            "limit": 5,
            "search_sources": True,
            "search_notes": False,
        },
    )
    ids = [str(x.get("parent_id") or x.get("id")) for x in r.json()["results"]]
    (evidence_dir / "F0d-7-search.txt").write_text(json.dumps(r.json())[:4000])
    assert any(sid in i for i in ids), ids
    assert not w.errs, w.errs


def test_f0d_3_stalled_status_poll(page: Page, api, notebook, evidence_dir):
    """Check 3: first status request after 10 s is held forever -> polling recovers."""
    w = Watch(page)
    title = f"f0d-stall-{int(time.time())}"
    held: dict = {}
    t0 = {"t": 1e18}

    def handler(route: Route) -> None:
        if not held and time.time() - t0["t"] >= 10:
            held["t"] = time.time()
            held["route"] = route
            return  # never answer
        route.continue_()

    page.route(STATUS_RE, handler)
    t0["t"] = time.time()
    t_add = _add_link(page, api, notebook, title)
    t0["t"] = t_add
    sid = _sid(api, notebook, title)
    t_done = _wait_card_done(page, title, 240)
    proc = _log_time(sid, "Successfully processed source")
    emb = _wait_log(sid, "Successfully embedded source", 180)
    after = w.reqs(held.get("t", 1e18) + 0.01)
    _timeline(
        evidence_dir,
        "3-stall",
        t_add,
        w,
        {
            "sid": sid,
            "held_s": held and round(held["t"] - t_add, 1),
            "next_req_after_held_s": after and round(after[0] - held["t"], 1),
            "card_done_s": t_done and round(t_done - t_add, 1),
            "processed_s": proc and round(proc[0] - t_add, 1),
            "embedded_s": round(emb[0] - t_add, 1),
        },
    )
    page.screenshot(path=str(evidence_dir / "F0d-3-stall-end.png"))
    assert held, "no status request held (polling never reached 10 s?)"
    assert after and after[0] - held["t"] <= 20, (
        "no new status request <= 20 s after held"
    )
    assert t_done, "card stuck after stalled poll"
    assert t_done - emb[0] <= 30, t_done - emb[0]
    assert proc, "no process log"
    assert t_done - proc[0] <= 30, t_done - proc[0]
    assert not w.reloads(t_add), w.reloads(t_add)
    _finish_checks(page, api, sid, title, evidence_dir, "3")


def test_f0d_4_hidden_tab(page: Page, api, notebook, evidence_dir):
    """Check 4: tab hidden 120 s right after add -> polls continue, card done on return."""
    w = Watch(page)
    title = f"f0d-hidden-{int(time.time())}"
    t_add = _add_link(page, api, notebook, title)
    _set_hidden(page, True)
    assert page.evaluate("document.visibilityState") == "hidden"
    sid = _sid(api, notebook, title)
    end = t_add + 120
    while time.time() < end:
        page.wait_for_timeout(1000)
    done_while_hidden = _card_done(page, title)
    proc = _log_time(sid, "Successfully processed source")
    hidden_reqs = w.reqs(t_add, end)
    _set_hidden(page, False)
    t_vis = time.time()
    t_done = _wait_card_done(page, title, 30)
    _timeline(
        evidence_dir,
        "4-hidden",
        t_add,
        w,
        {
            "sid": sid,
            "hidden_reqs": len(hidden_reqs),
            "processed_s": proc and round(proc[0] - t_add, 1),
            "done_while_hidden": done_while_hidden,
            "card_done_after_visible_s": t_done and round(t_done - t_vis, 1),
        },
    )
    page.screenshot(path=str(evidence_dir / "F0d-4-hidden-return.png"))
    assert proc, "process_source not finished in 120 s"
    assert len(w.reqs(t_add + 20, proc[0])) >= 5, "polling stopped while hidden"
    assert done_while_hidden, "card not done while hidden"
    assert t_done and t_done - t_vis <= 2, "card not done on return"
    assert not w.reloads(t_add), w.reloads(t_add)
    _finish_checks(page, api, sid, title, evidence_dir, "4")


def test_f0d_break_status_errors_then_recover(page: Page, api, notebook, evidence_dir):
    """Break: every status poll 503 from 5 s to 60 s (retries exhausted) -> polling resumes."""
    w = Watch(page)
    title = f"f0d-503-{int(time.time())}"
    win = {"a": 1e18, "b": 1e18}
    fails: list[float] = []

    def handler(route: Route) -> None:
        if win["a"] <= time.time() <= win["b"]:
            fails.append(time.time())
            route.fulfill(status=503, body="down")
            return
        route.continue_()

    page.route(STATUS_RE, handler)
    t_add = _add_link(page, api, notebook, title)
    win["a"], win["b"] = t_add + 5, t_add + 60
    sid = _sid(api, notebook, title)
    t_done = _wait_card_done(page, title, 240)
    proc = _log_time(sid, "Successfully processed source")
    after = w.reqs(win["b"])
    _timeline(
        evidence_dir,
        "break-503",
        t_add,
        w,
        {
            "sid": sid,
            "n_503": len(fails),
            "first_req_after_window_s": after and round(after[0] - win["b"], 1),
            "card_done_s": t_done and round(t_done - t_add, 1),
            "processed_s": proc and round(proc[0] - t_add, 1),
        },
    )
    page.screenshot(path=str(evidence_dir / "F0d-break-503-end.png"))
    assert len(fails) >= 5, fails
    assert t_done, "card stuck after status errors"
    assert proc and t_done - max(proc[0], win["b"]) <= 15, (t_done, proc, win)
    assert not w.reloads(t_add), w.reloads(t_add)
    errs = [e for e in w.errs if "503" not in e and "Failed to load resource" not in e]
    assert not errs, errs


def test_f0d_break_frozen_tab(page: Page, api, notebook, evidence_dir):
    """Break: Chrome freezes page (lifecycle 'frozen', like a backgrounded tab) 90 s -> resumes done."""
    w = Watch(page)
    title = f"f0d-frozen-{int(time.time())}"
    t_add = _add_link(page, api, notebook, title)
    cdp = page.context.new_cdp_session(page)
    _set_hidden(page, True)
    cdp.send("Page.setWebLifecycleState", {"state": "frozen"})
    sid = _sid(api, notebook, title)
    page.wait_for_timeout(90000)  # driver-side wait; keeps event timestamps real
    proc = _log_time(sid, "Successfully processed source")
    cdp.send("Page.setWebLifecycleState", {"state": "active"})
    _set_hidden(page, False)
    t_act = time.time()
    t_done = _wait_card_done(page, title, 30)
    _timeline(
        evidence_dir,
        "break-frozen",
        t_add,
        w,
        {
            "sid": sid,
            "processed_s": proc and round(proc[0] - t_add, 1),
            "card_done_after_active_s": t_done and round(t_done - t_act, 1),
        },
    )
    page.screenshot(path=str(evidence_dir / "F0d-break-frozen-end.png"))
    frozen_reqs = w.reqs(t_add + 5, t_act)
    assert proc, "process_source not finished in 90 s"
    assert t_done and t_done - t_act <= 10, "card not done after unfreeze"
    # info only: Chrome may ignore 'frozen' for a visible headless page
    print(f"polls during frozen window: {len(frozen_reqs)}")
