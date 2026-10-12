"""C1/F0 check 13 (UI): app loads, no console error, models page shows local models.

Plus:
- B1: AGENTS_SETUP.md tunnel claim must match how the browser reaches the API.
- B2: fresh-browser deep link must land on the requested page (no-auth mode).
"""

import re
from urllib.parse import urlparse

import pytest
from conftest import ROOT, WEB
from playwright.sync_api import Page, expect

IGNORE_CONSOLE = (
    "Download the React DevTools",
    "[Fast Refresh]",
    "[HMR]",
)


def _collect(page: Page):
    errors: list[str] = []
    bad_resp: list[str] = []
    api_hosts: set[str] = set()

    def on_console(m) -> None:
        if m.type == "error" and not any(s in m.text for s in IGNORE_CONSOLE):
            errors.append(m.text)

    page.on("console", on_console)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    def on_resp(r):
        if r.status >= 500:
            bad_resp.append(f"{r.status} {r.url}")

    def on_req(r):
        u = urlparse(r.url)
        if u.path.startswith("/api/"):
            api_hosts.add(u.netloc)

    page.on("response", on_resp)
    page.on("request", on_req)
    return errors, bad_resp, api_hosts


def test_f0_13_home_loads(page: Page, evidence_dir):
    errors, bad, _ = _collect(page)
    page.goto(WEB, wait_until="networkidle")
    page.wait_for_timeout(1500)
    page.screenshot(path=str(evidence_dir / "F0-home.png"), full_page=True)
    assert "/notebooks" in page.url, page.url
    expect(page.get_by_role("heading", name="Notebooks")).to_be_visible()
    assert not bad, bad
    assert not errors, errors


def test_f0_13_models_page(page: Page, evidence_dir):
    errors, bad, _ = _collect(page)
    page.goto(WEB, wait_until="networkidle")  # settle auth state first (see B2)
    page.get_by_role("link", name="Models").click()
    page.wait_for_url("**/settings/models")
    body = page.locator("body")
    expect(body).to_contain_text("local-llm", timeout=20000)
    expect(body).to_contain_text("local-embed", timeout=20000)
    page.wait_for_load_state("networkidle")
    page.screenshot(path=str(evidence_dir / "F0-models.png"), full_page=True)
    # direct reload of the page keeps working once auth state is stored
    page.reload(wait_until="networkidle")
    expect(body).to_contain_text("local-llm", timeout=20000)
    assert not bad, bad
    assert not errors, errors


def test_f0_b1_tunnel_doc_matches_api_path(page: Page):
    """If the browser calls :5055 directly, AGENTS_SETUP.md must not say only 3000 is needed."""
    _, _, hosts = _collect(page)
    page.goto(WEB, wait_until="networkidle")
    page.wait_for_timeout(1500)
    direct = any(h.endswith(":5055") for h in hosts)
    doc = (ROOT / "AGENTS_SETUP.md").read_text()
    claims_only_3000 = bool(re.search(r"[Oo]nly port 3000 is required", doc))
    assert not (direct and claims_only_3000), (
        f"browser API hosts {hosts} but AGENTS_SETUP.md says only port 3000 is required"
    )


@pytest.mark.xfail(
    reason="B2: LoginForm ignores redirectAfterLogin in no-auth mode", strict=False
)
def test_b2_fresh_deep_link_models(page: Page, evidence_dir):
    page.goto(f"{WEB}/settings/models", wait_until="networkidle")
    page.wait_for_timeout(3000)
    page.screenshot(path=str(evidence_dir / "B2-deeplink.png"), full_page=True)
    assert "/settings/models" in page.url, page.url
