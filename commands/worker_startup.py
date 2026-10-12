"""Housekeeping that runs once when the surreal-commands worker starts.

The worker imports this package (``--import-modules commands``) before it
starts its event loop and picks up queued jobs, so this is the one hook we
have into worker startup without wrapping the library CLI.
"""

import asyncio
import os
import sys

from loguru import logger

from open_notebook.domain.notebook import fail_orphaned_embed_jobs

WORKER_ENTRYPOINTS = ("surreal-commands-worker", "surreal_commands")


def is_worker_process(argv0: str | None = None) -> bool:
    """True when the current process is the surreal-commands worker CLI.

    The API imports ``commands`` too (to register commands); it must not
    touch running jobs, only the worker that owns them may.
    """
    name = os.path.basename(argv0 if argv0 is not None else sys.argv[0])
    return name.startswith(WORKER_ENTRYPOINTS)


def fail_orphaned_jobs_on_worker_start() -> None:
    """Close embed_source jobs a dead worker left 'running'.

    Jobs in flight die with the worker (restart, crash, OOM), but
    surreal-commands never reclaims 'running' jobs, so they would block their
    source forever. Never raises: a DB hiccup must not stop the worker.
    """
    if not is_worker_process():
        return
    try:
        asyncio.get_running_loop()
        logger.warning("Event loop already running; skipping orphaned job cleanup")
        return
    except RuntimeError:
        pass  # no loop yet: expected at worker import time
    try:
        failed = asyncio.run(fail_orphaned_embed_jobs())
    except Exception as e:
        logger.warning(f"Orphaned embed job cleanup failed: {e}")
        return
    if failed:
        logger.warning(
            f"Marked {len(failed)} embed_source job(s) left running by a previous "
            f"worker as failed: {', '.join(failed)}"
        )
