"""POST /api/embed must not queue a second embed_source job for a source whose
embed job is still queued/running — it returns the active job instead."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


@pytest.mark.parametrize("async_processing", [True, False])
def test_embed_source_returns_active_job(client, async_processing):
    source = MagicMock()
    source.vectorize = AsyncMock(return_value="command:active")
    with (
        patch(
            "api.routers.embedding.model_manager.get_embedding_model",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "api.routers.embedding.Source.get_active_embed_command",
            new=AsyncMock(return_value="command:active"),
        ),
        patch("api.routers.embedding.Source.get", new=AsyncMock(return_value=source)),
        patch(
            "api.routers.embedding.CommandService.submit_command_job",
            new=AsyncMock(return_value="command:new"),
        ) as mock_submit,
    ):
        resp = client.post(
            "/api/embed",
            json={
                "item_id": "source:abc",
                "item_type": "source",
                "async_processing": async_processing,
            },
        )

    assert resp.status_code == 200
    assert resp.json()["command_id"] == "command:active"
    mock_submit.assert_not_called()


# --- B5: parallel requests must not race past the active-job check ---------


@pytest.mark.asyncio
async def test_parallel_vectorize_submits_one_job():
    """Concurrent vectorize() calls for one source queue a single job."""
    import asyncio

    from open_notebook.domain.notebook import Source

    submitted: list[str] = []

    async def fake_query(query, vars=None):
        snapshot = list(submitted)  # DB read happens now...
        await asyncio.sleep(0.01)  # ...reply arrives later: callers interleave
        return snapshot

    def fake_submit(app, name, args):
        submitted.append("command:only")
        return "command:only"

    with (
        patch("open_notebook.domain.notebook.repo_query", new=fake_query),
        patch(
            "open_notebook.domain.notebook.submit_command", side_effect=fake_submit
        ) as mock_submit,
    ):
        results = await asyncio.gather(
            *[
                Source(id="source:race", title="t", full_text="text").vectorize()
                for _ in range(8)
            ]
        )

    assert mock_submit.call_count == 1
    assert set(results) == {"command:only"}


@pytest.mark.asyncio
async def test_rebuild_skips_sources_with_active_embed_job():
    from commands.embedding_commands import (
        RebuildEmbeddingsInput,
        rebuild_embeddings_command,
    )

    with (
        patch(
            "commands.embedding_commands.model_manager.get_embedding_model",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "commands.embedding_commands.collect_items_for_rebuild",
            new=AsyncMock(
                return_value={
                    "sources": ["source:busy", "source:idle"],
                    "notes": [],
                    "insights": [],
                }
            ),
        ),
        patch(
            "commands.embedding_commands.get_sources_with_active_embed",
            new=AsyncMock(return_value={"source:busy"}),
        ),
        patch(
            "commands.embedding_commands.submit_command", return_value="command:x"
        ) as mock_submit,
    ):
        result = await rebuild_embeddings_command(
            RebuildEmbeddingsInput(mode="existing")
        )

    mock_submit.assert_called_once_with(
        "open_notebook", "embed_source", {"source_id": "source:idle"}
    )
    assert result.sources_submitted == 1


# --- B4: worker restart must not leave embed jobs 'running' forever ---------


@pytest.mark.parametrize(
    "argv0,expected",
    [
        ("/app/.venv/bin/surreal-commands-worker", True),
        ("surreal-commands-worker.exe", True),
        ("/app/run_api.py", False),
        ("/app/.venv/bin/pytest", False),
    ],
)
def test_is_worker_process(argv0, expected):
    from commands.worker_startup import is_worker_process

    assert is_worker_process(argv0) is expected


def test_orphan_cleanup_skipped_outside_worker():
    from commands import worker_startup

    with (
        patch.object(worker_startup, "is_worker_process", return_value=False),
        patch.object(
            worker_startup, "fail_orphaned_embed_jobs", new=AsyncMock()
        ) as mock_fail,
    ):
        worker_startup.fail_orphaned_jobs_on_worker_start()
    mock_fail.assert_not_called()


def test_orphan_cleanup_runs_in_worker_and_never_raises():
    from commands import worker_startup

    with (
        patch.object(worker_startup, "is_worker_process", return_value=True),
        patch.object(
            worker_startup,
            "fail_orphaned_embed_jobs",
            new=AsyncMock(return_value=["command:dead"]),
        ) as mock_fail,
    ):
        worker_startup.fail_orphaned_jobs_on_worker_start()
    mock_fail.assert_awaited_once()

    with (
        patch.object(worker_startup, "is_worker_process", return_value=True),
        patch.object(
            worker_startup,
            "fail_orphaned_embed_jobs",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ),
    ):
        worker_startup.fail_orphaned_jobs_on_worker_start()  # no raise


@pytest.mark.asyncio
async def test_fail_orphaned_embed_jobs_only_touches_running_embed_source():
    from open_notebook.domain.notebook import fail_orphaned_embed_jobs

    mock_query = AsyncMock(return_value=["command:dead"])
    with patch("open_notebook.domain.notebook.repo_query", new=mock_query):
        assert await fail_orphaned_embed_jobs() == ["command:dead"]
    query = mock_query.call_args.args[0]
    assert "name = 'embed_source'" in query
    assert "status = 'running'" in query
    assert "status = 'failed'" in query
