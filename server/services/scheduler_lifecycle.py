"""Periodic recovery independent of the workflow worker pool."""

import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from server.services.run_scheduler import run_scheduler

logger = logging.getLogger(__name__)
RECONCILE_INTERVAL_SECONDS = 5
SHUTDOWN_TIMEOUT_SECONDS = 5


def _reconcile_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            run_scheduler.reconcile()
        except Exception:
            logger.exception("Run scheduler reconciliation failed; retrying next interval")
        stop.wait(RECONCILE_INTERVAL_SECONDS)


@asynccontextmanager
async def scheduler_lifespan(app):
    stop = threading.Event()
    thread = threading.Thread(
        target=_reconcile_loop, args=(stop,), name="run-reconciler", daemon=True
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        await asyncio.to_thread(thread.join, SHUTDOWN_TIMEOUT_SECONDS)
        if thread.is_alive():
            logger.warning("Run reconciliation still finishing during API shutdown")
