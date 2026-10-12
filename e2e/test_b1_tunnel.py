"""B1 verify: AGENTS_SETUP.md tunnel instructions match real browser behaviour.

Sim of user laptop via localhost:
- both ports (3000 + 5055) reachable -> UI loads data (doc main path)
- only 3000 reachable (abort browser req to :5055) -> "Unable to Connect" (doc warning)
- alt port 13000 -> 3000 (TCP forwarder) + 5055 -> UI loads data (doc alt path)
"""

import re
import socket
import threading
from urllib.parse import urlparse

import httpx
import pytest
from conftest import ROOT, WEB
from playwright.sync_api import Page, expect

ALT_PORT = 13000
WEB_PORT = urlparse(WEB).port or 3000
UNABLE = "Unable to Connect to API Server"


def _pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            d = a.recv(65536)
            if not d:
                break
            b.sendall(d)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


@pytest.fixture
def alt_forward():
    """localhost:13000 -> 127.0.0.1:3000, like `ssh -L 13000:127.0.0.1:3000`."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("127.0.0.1", ALT_PORT))
    except OSError:
        pytest.skip(f"port {ALT_PORT} busy")
    srv.listen(64)
    stop = threading.Event()

    def accept_loop():
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except (TimeoutError, OSError):
                continue
            u = socket.create_connection(("127.0.0.1", WEB_PORT))
            threading.Thread(target=_pipe, args=(c, u), daemon=True).start()
            threading.Thread(target=_pipe, args=(u, c), daemon=True).start()

    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()
    yield f"http://localhost:{ALT_PORT}"
    stop.set()
    srv.close()


def _open_models(page: Page, base: str) -> tuple[set[str], list[str]]:
    hosts: set[str] = set()
    errs: list[str] = []

    def on_req(r) -> None:
        if urlparse(r.url).path.startswith("/api/"):
            hosts.add(urlparse(r.url).netloc)

    page.on("request", on_req)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(base, wait_until="networkidle")
    page.wait_for_timeout(1500)
    page.get_by_role("link", name="Models").click()
    page.wait_for_url("**/settings/models")
    return hosts, errs


def test_b1_doc_text():
    doc = (ROOT / "AGENTS_SETUP.md").read_text()
    assert not re.search(r"[Oo]nly port 3000 is required", doc)
    assert "-L 3000:127.0.0.1:3000 -L 5055:127.0.0.1:5055" in doc
    assert "LocalForward 5055 127.0.0.1:5055" in doc
    assert "keep `-L 5055:127.0.0.1:5055`" in doc


@pytest.mark.parametrize("host", ["localhost:3000", "localhost:13000"])
def test_b1_config_api_url(host):
    r = httpx.get(f"{WEB}/config", headers={"Host": host}, timeout=30)
    assert r.json()["apiUrl"] == "http://localhost:5055"


def test_b1_both_ports(page: Page, evidence_dir):
    base = f"http://localhost:{WEB_PORT}"
    hosts, errs = _open_models(page, base)
    body = page.locator("body")
    expect(body).to_contain_text("local-llm", timeout=20000)
    page.screenshot(path=str(evidence_dir / "B1-verify-both-ports.png"), full_page=True)
    assert "localhost:5055" in hosts, hosts
    assert UNABLE not in body.inner_text()
    assert not errs, errs


def test_b1_only_3000_fails_as_doc_warns(page: Page, evidence_dir):
    page.route(re.compile(r"^https?://[^/]+:5055/"), lambda r: r.abort())
    page.goto(f"http://localhost:{WEB_PORT}/settings/models", wait_until="networkidle")
    expect(page.locator("body")).to_contain_text(UNABLE, timeout=30000)
    page.screenshot(path=str(evidence_dir / "B1-verify-only3000.png"), full_page=True)


def test_b1_alt_13000(page: Page, evidence_dir, alt_forward):
    hosts, errs = _open_models(page, alt_forward)
    body = page.locator("body")
    expect(body).to_contain_text("local-llm", timeout=20000)
    page.screenshot(path=str(evidence_dir / "B1-verify-alt13000.png"), full_page=True)
    assert "localhost:5055" in hosts, hosts
    assert UNABLE not in body.inner_text()
    assert not errs, errs
