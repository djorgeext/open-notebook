"""B3 verify (F0b) UI: page knows a running embed job after reload / leave+return /
second tab; double click makes one job; resumed poll reports the result.

Uses a copy of the HRV source text (88 chunks, ~5 min job) so the job is long enough.
"""

import re
import time

import pytest
from conftest import WEB, active_embed_jobs, sql
from playwright.sync_api import Page, expect

HRV_SOURCE = "source:v4e42282fh9zk3gfhxum"
MENU_BTN = (
    "button:has(svg.lucide-more-vertical), button:has(svg.lucide-ellipsis-vertical)"
)
IGNORE_CONSOLE = ("Download the React DevTools", "[Fast Refresh]", "[HMR]")


@pytest.fixture
def long_source(api):
    text = api.get(f"/api/sources/{HRV_SOURCE}").json()["full_text"]
    assert len(text) > 50000
    r = api.post(
        "/api/sources",
        data={
            "type": "text",
            "content": text,
            "title": f"b3-ui-{int(time.time())}",
            "embed": "false",
            "async_processing": "false",
        },
    )
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    yield sid
    api.delete(f"/api/sources/{sid}")


def _jobs(sid: str) -> list[dict]:
    return sql(
        f"SELECT id, status FROM command WHERE name='embed_source' "
        f"AND args.source_id = '{sid}';"
    )[0]


def _details(page: Page, sid: str):
    page.goto(f"{WEB}/sources/{sid}", wait_until="networkidle")
    page.get_by_role("tab", name="Details").click()


def _assert_busy(page: Page, tag: str, evidence_dir):
    busy = page.get_by_role("button", name="Embedding...")
    expect(busy).to_be_visible(timeout=15000)
    expect(busy).to_be_disabled()
    expect(page.get_by_role("button", name="Embed Content")).to_have_count(0)
    page.screenshot(path=str(evidence_dir / f"B3-verify-{tag}.png"), full_page=True)


def test_b3_ui_reload_leave_tab_doubleclick(page: Page, api, long_source, evidence_dir):
    sid = long_source
    errs: list[str] = []

    def on_console(m) -> None:
        if m.type == "error" and not any(s in m.text for s in IGNORE_CONSOLE):
            errs.append(m.text)

    page.on("console", on_console)
    page.goto(WEB, wait_until="networkidle")  # settle auth (B2)
    _details(page, sid)
    expect(page.get_by_text("Content Not Embedded")).to_be_visible(timeout=20000)

    # double click + extra clicks -> one job
    btn = page.get_by_role("button", name="Embed Content")
    btn.dblclick()
    expect(page.get_by_text("Embedding started")).to_be_visible(timeout=10000)
    time.sleep(2)
    jobs = _jobs(sid)
    assert len(jobs) == 1, jobs
    cmd = str(jobs[0]["id"])

    # reload mid-job
    page.reload(wait_until="networkidle")
    page.get_by_role("tab", name="Details").click()
    _assert_busy(page, "reload", evidence_dir)

    # menu item also busy
    page.locator(MENU_BTN).first.click()
    item = page.get_by_role("menuitem", name=re.compile("Embedding"))
    expect(item).to_be_visible()
    expect(item).to_have_attribute("data-disabled", "")
    page.keyboard.press("Escape")

    # leave + come back
    page.goto(f"{WEB}/notebooks", wait_until="networkidle")
    _details(page, sid)
    _assert_busy(page, "return", evidence_dir)

    # second tab
    p2 = page.context.new_page()
    _details(p2, sid)
    _assert_busy(p2, "tab2", evidence_dir)
    p2.close()

    # API double submit -> same job
    r = api.post("/api/embed", json={"item_id": sid, "item_type": "source"})
    assert r.json()["command_id"] == cmd, r.json()
    assert len(_jobs(sid)) == 1

    # resumed poll reports result on reloaded page
    expect(page.get_by_text("Content embedded. It is now searchable.")).to_be_visible(
        timeout=900000
    )
    expect(page.get_by_text("Content Not Embedded")).to_have_count(0, timeout=15000)
    page.screenshot(path=str(evidence_dir / "B3-verify-done.png"), full_page=True)
    src = api.get(f"/api/sources/{sid}").json()
    assert src["embedded"] is True and src["embed_command_id"] is None, src
    assert 80 <= src["embedded_chunks"] <= 96, src["embedded_chunks"]
    assert len(_jobs(sid)) == 1 and not active_embed_jobs(sid)
    assert not errs, errs
