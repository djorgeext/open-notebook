"""F0d side findings (API). B9, B10, B11 — xfail strict: remove marker when fixed.

B9  GET /api/sources/{id} bumps source.updated (view stamp UPDATE fires VALUE time::now()).
B10 link to a PDF URL without title -> title is content-core temp file name (tmpXXXX.00088).
B11 delete source while embed/insight jobs run -> orphan source_embedding + source_insight
    rows; ghost insights show up in vector search with parent_id None. ~2 min.
"""

import subprocess
import time

import pytest
from conftest import sql

ARXIV_URL = "https://arxiv.org/pdf/2610.00088"
NET = (
    subprocess.run(
        ["curl", "-sfI", "-m", "20", ARXIV_URL], capture_output=True
    ).returncode
    == 0
)


def _jobs(sid: str) -> list:
    return sql(
        f"SELECT id FROM command WHERE args.source_id = '{sid}' "
        "AND status IN ['new', 'queued', 'running'];"
    )[0]


@pytest.fixture
def link_source(api):
    made: list[str] = []

    def make(**form) -> dict:
        r = api.post("/api/sources", data={"type": "link", "url": ARXIV_URL, **form})
        assert r.status_code == 200, r.text
        made.append(r.json()["id"])
        return r.json()

    yield make
    for sid in made:
        end = time.time() + 300
        while _jobs(sid) and time.time() < end:
            time.sleep(3)
        api.delete(f"/api/sources/{sid}")
        # tester cleanup of own rows (B11 leaves these behind)
        sql(
            f"DELETE source_embedding WHERE source = {sid}; "
            f"DELETE source_insight WHERE source = {sid};"
        )


@pytest.mark.skipif(not NET, reason="no net to arxiv")
@pytest.mark.xfail(strict=True, reason="B9: plain GET bumps source.updated")
def test_b9_get_source_does_not_bump_updated(api, link_source):
    sid = link_source(embed="false", async_processing="false", title="b9-check")["id"]
    u0 = sql(f"SELECT updated FROM {sid};")[0][0]["updated"]
    time.sleep(1.5)
    assert api.get(f"/api/sources/{sid}").status_code == 200
    u1 = sql(f"SELECT updated FROM {sid};")[0][0]["updated"]
    assert u0 == u1, (u0, u1)


@pytest.mark.skipif(not NET, reason="no net to arxiv")
@pytest.mark.xfail(strict=True, reason="B10: PDF link title = temp file name")
def test_b10_pdf_link_title_not_temp_name(link_source):
    src = link_source(embed="false", async_processing="false")
    assert not src["title"].startswith("tmp"), src["title"]


@pytest.mark.skipif(not NET, reason="no net to arxiv")
@pytest.mark.xfail(strict=True, reason="B11: delete mid-job leaves orphan rows")
def test_b11_delete_during_jobs_leaves_no_orphans(api):
    dense = [
        t["id"]
        for t in api.get("/api/transformations").json()
        if t["title"] == "Dense Summary"
    ][0]
    r = api.post(
        "/api/sources",
        data={
            "type": "link",
            "url": ARXIV_URL,
            "title": "b11-check",
            "embed": "true",
            "async_processing": "true",
            "transformations": f'["{dense}"]',
        },
    )
    sid = r.json()["id"]
    try:
        end = time.time() + 300
        while api.get(f"/api/sources/{sid}/status").json()["status"] != "completed":
            assert time.time() < end, "process_source not done"
            time.sleep(2)
        assert _jobs(sid), "embed job already finished; cannot test mid-job delete"
        assert api.delete(f"/api/sources/{sid}").status_code == 200
        end = time.time() + 300
        while _jobs(sid) and time.time() < end:
            time.sleep(3)
        chunks = sql(
            f"SELECT count() FROM source_embedding WHERE source = {sid} GROUP ALL;"
        )[0]
        ins = sql(f"SELECT id FROM source_insight WHERE source = {sid};")[0]
        assert not chunks and not ins, (chunks, ins)
    finally:
        sql(
            f"DELETE source_embedding WHERE source = {sid}; "
            f"DELETE source_insight WHERE source = {sid}; DELETE {sid};"
        )
