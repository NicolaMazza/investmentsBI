from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def start() -> None:
    # Jobs are registered in M7; placeholder for M1.
    scheduler.start()
    log.info("Scheduler started")


def shutdown() -> None:
    scheduler.shutdown(wait=False)
    log.info("Scheduler stopped")
