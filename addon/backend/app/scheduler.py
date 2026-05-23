"""APScheduler configuration.

Each job fires at its own configurable time (add-on options):

    snapshot_local_time  (default 00:00)  — position_snapshot
    job_time_ishares     (default 00:05)  — ishares_holdings
    job_time_etf         (default 00:10)  — etf_holdings  (Playwright, ~1 min)
    job_time_aggregate   (default 00:20)  — aggregate_allocation

Order matters: position_snapshot must finish before aggregate_allocation
reads tonight's positions.  The defaults guarantee a safe gap on typical
hardware; adjust if your machine is slower or you want daytime runs.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings

log = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def _cron(t: str, label: str) -> CronTrigger:
    """Parse 'HH:MM' into a daily CronTrigger.  Falls back to 00:00 on error."""
    try:
        h, m = t.strip().split(":")
        return CronTrigger(hour=int(h) % 24, minute=int(m) % 60)
    except Exception:
        log.warning("scheduler: invalid time %r for %s, defaulting to 00:00", t, label)
        return CronTrigger(hour=0, minute=0)


def start() -> None:
    from app.jobs import (
        aggregate_allocation,
        etf_holdings,
        ishares_holdings,
        market_cap_enrichment,
        position_snapshot,
    )

    scheduler.add_job(
        position_snapshot.run,
        _cron(settings.snapshot_local_time, "position_snapshot"),
        id="position_snapshot", replace_existing=True,
    )
    scheduler.add_job(
        ishares_holdings.run,
        _cron(settings.job_time_ishares, "ishares_holdings"),
        id="ishares_holdings", replace_existing=True,
    )
    scheduler.add_job(
        etf_holdings.run,
        _cron(settings.job_time_etf, "etf_holdings"),
        id="etf_holdings", replace_existing=True,
    )
    scheduler.add_job(
        aggregate_allocation.run,
        _cron(settings.job_time_aggregate, "aggregate_allocation"),
        id="aggregate_allocation", replace_existing=True,
    )

    # Market cap enrichment runs weekly on Sunday at job_time_market_cap.
    mc_h, mc_m = (0, 0)
    try:
        mc_h, mc_m = (int(x) for x in settings.job_time_market_cap.split(":"))
    except Exception:
        pass
    scheduler.add_job(
        market_cap_enrichment.run,
        CronTrigger(day_of_week="sun", hour=mc_h, minute=mc_m),
        id="market_cap_enrichment", replace_existing=True,
    )

    scheduler.start()
    log.info(
        "Scheduler started — position_snapshot@%s  ishares@%s  etf@%s  "
        "aggregate@%s  market_cap@sun%s",
        settings.snapshot_local_time,
        settings.job_time_ishares,
        settings.job_time_etf,
        settings.job_time_aggregate,
        settings.job_time_market_cap,
    )


def shutdown() -> None:
    scheduler.shutdown(wait=False)
    log.info("Scheduler stopped")
