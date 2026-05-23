"""APScheduler configuration.

All three nightly jobs run sequentially at the local time specified by
``snapshot_local_time`` in the add-on options (default 00:00).

Job order matters: position_snapshot must run before aggregate_allocation
so that tonight's positions are available when allocations are computed.

    00:00  position_snapshot    — pull today's positions from Ghostfolio DB
    00:05  ishares_holdings     — refresh iShares XLSX composition
    00:10  etf_holdings         — refresh Vanguard + HSBC composition (Playwright)
    00:20  aggregate_allocation — pre-compute portfolio_allocation_snapshot rows

The 5-/10-/20-minute offsets are hard-coded relative to snapshot_local_time
so that even on a slow machine the upstream jobs have time to finish.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings

log = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def _parse_hhmm(t: str) -> tuple[int, int]:
    """Parse 'HH:MM' → (hour, minute).  Falls back to (0, 0) on bad input."""
    try:
        h, m = t.strip().split(":")
        return int(h) % 24, int(m) % 60
    except Exception:
        log.warning("scheduler: invalid snapshot_local_time %r, defaulting to 00:00", t)
        return 0, 0


def start() -> None:
    from app.jobs import (
        aggregate_allocation,
        etf_holdings,
        ishares_holdings,
        position_snapshot,
    )

    base_h, base_m = _parse_hhmm(settings.snapshot_local_time)

    def _trigger(extra_minutes: int = 0) -> CronTrigger:
        total = base_h * 60 + base_m + extra_minutes
        return CronTrigger(hour=total // 60 % 24, minute=total % 60)

    scheduler.add_job(position_snapshot.run,    _trigger(0),  id="position_snapshot",    replace_existing=True)
    scheduler.add_job(ishares_holdings.run,     _trigger(5),  id="ishares_holdings",     replace_existing=True)
    scheduler.add_job(etf_holdings.run,         _trigger(10), id="etf_holdings",         replace_existing=True)
    scheduler.add_job(aggregate_allocation.run, _trigger(20), id="aggregate_allocation", replace_existing=True)

    scheduler.start()

    def _fmt(extra: int) -> str:
        t = base_h * 60 + base_m + extra
        return f"{t // 60 % 24:02d}:{t % 60:02d}"

    log.info(
        "Scheduler started — position_snapshot@%s  ishares@%s  etf@%s  aggregate@%s",
        _fmt(0), _fmt(5), _fmt(10), _fmt(20),
    )


def shutdown() -> None:
    scheduler.shutdown(wait=False)
    log.info("Scheduler stopped")
