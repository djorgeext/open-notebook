"""B6/B7/B8 break tests (F0b, embed svc bounded priority). Direct to embed svc, no app data.

- B6: two big requests at once under 8-client small spam (1- and 2-text) both finish bounded
- B7: 4 concurrent small callers during a big request -> every small bounded (fair among smalls)
- B8: 48 concurrent small clients (> anyio 40 thread pool) + big: /health and /v1/models answer < 5 s

Run (~5 min): uv run --with pytest-playwright --with httpx pytest e2e/test_b6_break.py -q -s --browser-channel chrome
"""

import concurrent.futures as cf
import threading
import time

import httpx
import pytest
from conftest import EMBED

TEXT = ("heart rate variability sensor wearable " * 40)[:1100]
BIG = [TEXT] * 16


def _big(texts: list[str] = BIG) -> float:
    t = time.time()
    r = httpx.post(f"{EMBED}/v1/embeddings", json={"input": texts}, timeout=900)
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]) == len(texts)
    return time.time() - t


def _spam(n: int, stop: threading.Event, out: dict, errs: list, mixed: bool = True):
    def run(i: int):
        two = mixed and i % 2 == 1
        inp = ["query heart", "wrist sensor hz"] if two else "query heart"
        while not stop.is_set():
            t = time.time()
            try:
                r = httpx.post(
                    f"{EMBED}/v1/embeddings", json={"input": inp}, timeout=600
                )
                if r.status_code != 200:
                    errs.append(r.status_code)
            except httpx.HTTPError as e:
                errs.append(repr(e))
            out.setdefault(i, []).append(time.time() - t)

    ths = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(n)]
    for th in ths:
        th.start()
    return ths


def _stop(stop: threading.Event, ths: list):
    stop.set()
    for th in ths:
        th.join(timeout=120)


def test_b6_break_two_big_under_spam(evidence_dir):
    with cf.ThreadPoolExecutor(2) as ex:
        t = time.time()
        list(ex.map(lambda _: _big(), range(2)))
        two_alone = time.time() - t
    stop = threading.Event()
    small: dict[int, list[float]] = {}
    errs: list[str] = []
    ths = _spam(8, stop, small, errs)
    time.sleep(1)
    try:
        with cf.ThreadPoolExecutor(2) as ex:
            bigs = list(ex.map(lambda _: _big(), range(2)))
    finally:
        _stop(stop, ths)
    s = sorted(v for lst in small.values() for v in lst)
    (evidence_dir / "B6-break-two-big.txt").write_text(
        f"2x16-text concurrent alone {two_alone:.1f}s; under 8x small spam "
        f"{[round(b, 1) for b in bigs]}s; small n={len(s)} p50 {s[len(s) // 2]:.2f}s "
        f"p95 {s[int(len(s) * 0.95)]:.2f}s max {s[-1]:.2f}s; errs {errs[:5]}\n"
    )
    assert not errs, errs[:5]
    assert max(bigs) < max(90, two_alone * 3), (bigs, two_alone)


@pytest.mark.xfail(
    strict=True, reason="B7: small callers not FIFO, one can wait ~whole big job"
)
def test_b7_small_callers_fair_during_big(evidence_dir):
    stop = threading.Event()
    small: dict[int, list[float]] = {}
    errs: list[str] = []
    ths = _spam(4, stop, small, errs, mixed=False)
    time.sleep(1)
    try:
        big = _big([TEXT] * 48)
    finally:
        _stop(stop, ths)
    per = {i: (len(v), round(max(v), 2)) for i, v in small.items()}
    (evidence_dir / "B7-verify.txt").write_text(
        f"4 small clients during 48-text ({big:.1f}s): per client (n, max s) {per}; "
        f"errs {errs[:5]}\n"
    )
    assert not errs, errs[:5]
    # one round = HIGH_BURST smalls + one 4-text slice (~3.8 s); 4 clients -> <= 2 rounds
    assert max(m for _, m in per.values()) < 10, per


@pytest.mark.xfail(
    strict=True, reason="B8: sync /health shares 40-thread pool with blocked embeds"
)
def test_b8_health_answers_under_pool_saturation(evidence_dir):
    stop = threading.Event()
    small: dict[int, list[float]] = {}
    errs: list[str] = []
    ths = _spam(48, stop, small, errs)
    time.sleep(2)
    big_t: list[float] = []
    bt = threading.Thread(target=lambda: big_t.append(_big()))
    bt.start()
    health = []
    try:
        for _ in range(8):
            t = time.time()
            ok: bool | str = True
            try:
                h = httpx.get(f"{EMBED}/health", timeout=30).status_code
                m = httpx.get(f"{EMBED}/v1/models", timeout=30).status_code
                ok = h == 200 and m == 200
            except httpx.HTTPError as e:
                ok = repr(e)
            health.append((round(time.time() - t, 2), ok))
            time.sleep(2)
        bt.join(timeout=600)
    finally:
        _stop(stop, ths)
    s = sorted(v for lst in small.values() for v in lst)
    (evidence_dir / "B8-verify.txt").write_text(
        f"48 small clients + 16-text: big {big_t}s; health+models (s, ok) {health}; "
        f"small n={len(s)} p50 {s[len(s) // 2]:.2f}s max {s[-1]:.2f}s; errs {errs[:5]}\n"
    )
    assert big_t and big_t[0] < 120, big_t
    assert all(ok is True for _, ok in health), health
    assert max(d for d, _ in health) < 5, health  # stack.sh status: curl -m 5
