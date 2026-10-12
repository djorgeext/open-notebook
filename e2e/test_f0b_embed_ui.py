"""F0b UI: "Embed Content" shows progress, result, and failures. Plan: .agents/plans/F0b.md checks 5, 6, 8.

Fixture PDF (e2e/fixtures/f0b_tiny.pdf) is served from a local http server and added as a
link source with embed off. Check 6 stops the embed svc (ops/stack.sh down embed) and
always brings it back.
"""

import functools
import http.server
import re
import subprocess
import threading
import time

import pytest
from conftest import ROOT, WEB
from playwright.sync_api import Page, expect

FIX = ROOT / "e2e" / "fixtures"
WORKER_LOG = ROOT / ".run" / "worker.log"
MENU_BTN = (
    "button:has(svg.lucide-more-vertical), button:has(svg.lucide-ellipsis-vertical)"
)
IGNORE_CONSOLE = ("Download the React DevTools", "[Fast Refresh]", "[HMR]")


@pytest.fixture(scope="module")
def pdf_url():
    if not (FIX / "f0b_tiny.pdf").exists():
        subprocess.run(["python3", str(FIX / "make_tiny_pdf.py")], check=True)
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(FIX)
    )
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/f0b_tiny.pdf"
    srv.shutdown()


@pytest.fixture
def tiny_source(api, pdf_url):
    ids = []

    def make(title: str) -> str:
        r = api.post(
            "/api/sources",
            data={
                "type": "link",
                "url": pdf_url,
                "title": title,
                "embed": "false",
                "async_processing": "false",
            },
        )
        assert r.status_code == 200, r.text
        src = r.json()
        assert "zorblat" in (src.get("full_text") or ""), src.get("full_text")
        assert src["embedded"] is False
        ids.append(src["id"])
        return src["id"]

    yield make
    for i in ids:
        api.delete(f"/api/sources/{i}")


def _stack(cmd: str, svc: str):
    r = subprocess.run(
        ["bash", "ops/stack.sh", cmd, svc], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr


def _console(page: Page) -> list[str]:
    errs: list[str] = []

    def on_console(m) -> None:
        if m.type == "error" and not any(s in m.text for s in IGNORE_CONSOLE):
            errs.append(m.text)

    page.on("console", on_console)
    page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    return errs


def _open_details(page: Page, sid: str):
    page.goto(WEB, wait_until="networkidle")  # settle auth first (B2)
    page.goto(f"{WEB}/sources/{sid}", wait_until="networkidle")
    page.get_by_role("tab", name="Details").click()
    expect(page.get_by_text("Content Not Embedded")).to_be_visible(timeout=20000)


def test_f0b_5_embed_button_progress_and_done(
    page: Page, api, tiny_source, evidence_dir
):
    errs = _console(page)
    sid = tiny_source("f0b-ui-ok")
    _open_details(page, sid)

    btn = page.get_by_role("button", name=re.compile(r"Embed Content|Embedding\.\.\."))
    btn.click()
    expect(page.get_by_text("Embedding started")).to_be_visible(timeout=10000)
    page.screenshot(path=str(evidence_dir / "F0b-T5-started.png"), full_page=True)

    expect(page.get_by_text("Content embedded. It is now searchable.")).to_be_visible(
        timeout=300000
    )
    expect(page.get_by_text("Content Not Embedded")).to_have_count(0, timeout=15000)
    page.locator(MENU_BTN).first.click()
    item = page.get_by_role("menuitem", name="Already Embedded")
    expect(item).to_be_visible()
    expect(item).to_have_attribute("data-disabled", "")
    page.screenshot(path=str(evidence_dir / "F0b-T5-done.png"), full_page=True)
    page.keyboard.press("Escape")

    src = api.get(f"/api/sources/{sid}").json()
    assert src["embedded"] is True and src["embedded_chunks"] >= 1, src
    r = api.post(
        "/api/search",
        json={
            "query": "zorblat sensor 47 hertz",
            "type": "vector",
            "limit": 5,
            "search_sources": True,
            "search_notes": False,
        },
    )
    assert any(
        sid in str(x.get("parent_id") or x.get("id")) for x in r.json()["results"]
    ), r.json()
    assert not errs, errs


def test_f0b_6_8_embed_fail_shows_error(page: Page, api, tiny_source, evidence_dir):
    errs = _console(page)
    sid = tiny_source("f0b-ui-fail")
    log_mark = WORKER_LOG.stat().st_size
    _open_details(page, sid)
    _stack("down", "embed")
    try:
        page.get_by_role("button", name="Embed Content").click()
        expect(page.get_by_text("Embedding started")).to_be_visible(timeout=10000)
        running = page.get_by_role("button", name="Embedding...")
        expect(running).to_be_visible()
        expect(running).to_be_disabled()
        time.sleep(4)  # still running after a poll cycle
        expect(running).to_be_disabled()
        page.screenshot(path=str(evidence_dir / "F0b-T6-running.png"), full_page=True)

        toast = page.locator("[data-sonner-toast][data-type='error']")
        expect(toast).to_be_visible(timeout=480000)
        msg = toast.inner_text()
        page.screenshot(path=str(evidence_dir / "F0b-T6-failed.png"), full_page=True)
        (evidence_dir / "F0b-T6-toast.txt").write_text(msg)
        assert re.search(r"Failed to generate embeddings.*\w+Error", msg, re.S), msg
        assert not re.search(r":\s*$", msg.strip()), f"blank after colon: {msg!r}"
        expect(page.get_by_role("button", name="Embed Content")).to_be_enabled()
    finally:
        _stack("up", "embed")

    # check 8: worker log names the exception type, not empty after the colon
    with open(WORKER_LOG, "rb") as f:
        f.seek(log_mark)
        log = f.read().decode(errors="replace")
    lines = [
        ln
        for ln in log.splitlines()
        if "Failed to generate embeddings using model" in ln
    ]
    (evidence_dir / "F0b-T8-worker.txt").write_text("\n".join(lines[-5:]))
    assert lines, "no embed failure line in worker.log"
    assert all(
        re.search(r"texts\): \w+(Error|Exception|Timeout)", ln) for ln in lines
    ), lines[-3:]
    # failure console error from waitForCommand is expected; nothing else
    other = [
        e
        for e in errs
        if "Command failed" not in e and "Failed to load resource" not in e
    ]
    assert not other, other


ARXIV_URL = "https://arxiv.org/pdf/2608.18711"  # real paper (HRV, ~88 chunks)


@pytest.mark.skipif(
    subprocess.run(
        ["curl", "-sfI", "-m", "15", ARXIV_URL], capture_output=True
    ).returncode
    != 0,
    reason="no net to arxiv",
)
def test_f0b_5_real_arxiv_add_link_then_embed(page: Page, api, evidence_dir):
    """User flow: New > Source > URL (embed off, no transformations) > Done > open > Embed Content."""
    title = f"f0b-arxiv-{int(time.time())}"
    errs = _console(page)
    page.goto(WEB, wait_until="networkidle")
    page.get_by_role("button", name="New", exact=True).click()
    page.get_by_role("menuitem", name="Source").click()
    page.get_by_role("textbox", name="URL(s) *").fill(ARXIV_URL)
    page.get_by_role("textbox", name="Title (Optional)").fill(title)
    page.get_by_role("button", name="Next", exact=True).click()
    page.get_by_role("button", name="Next", exact=True).click()
    checked = page.locator("[role=dialog] [role=checkbox][data-state=checked]")
    for _ in range(10):  # no transformations, embedding off
        if checked.count() == 0:
            break
        checked.first.click()
    expect(
        page.locator("[role=dialog] [role=checkbox][data-state=checked]")
    ).to_have_count(0)
    page.get_by_role("button", name="Done", exact=True).click()

    sid = None
    end = time.time() + 300
    while time.time() < end and not sid:
        rows = api.get("/api/sources", params={"limit": 20}).json()
        hit = [
            s for s in rows if s["title"] == title and s.get("status") == "completed"
        ]
        sid = hit[0]["id"] if hit else None
        time.sleep(3)
    assert sid, f"source {title} not processed in 300s"
    try:
        src = api.get(f"/api/sources/{sid}").json()
        assert src["embedded"] is False and len(src.get("full_text") or "") > 20000
        _open_details(page, sid)
        page.get_by_role("button", name="Embed Content").click()
        expect(page.get_by_text("Embedding started")).to_be_visible(timeout=10000)
        running = page.get_by_role("button", name="Embedding...")
        expect(running).to_be_disabled()
        page.screenshot(
            path=str(evidence_dir / "F0b-T5-arxiv-running.png"), full_page=True
        )
        expect(
            page.get_by_text("Content embedded. It is now searchable.")
        ).to_be_visible(timeout=600000)
        expect(page.get_by_text("Content Not Embedded")).to_have_count(0, timeout=15000)
        page.screenshot(
            path=str(evidence_dir / "F0b-T5-arxiv-done.png"), full_page=True
        )
        src = api.get(f"/api/sources/{sid}").json()
        assert src["embedded"] is True and 80 <= src["embedded_chunks"] <= 96, src[
            "embedded_chunks"
        ]
        r = api.post(
            "/api/search",
            json={
                "query": "heart rate variability egocentric video",
                "type": "vector",
                "limit": 10,
                "search_sources": True,
                "search_notes": False,
            },
        )
        ids = [str(x.get("parent_id") or x.get("id")) for x in r.json()["results"]]
        assert any(sid in i for i in ids), ids
        assert not errs, errs
    finally:
        api.delete(f"/api/sources/{sid}")
