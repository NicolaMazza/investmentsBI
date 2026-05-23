from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from sqlalchemy import select

log = logging.getLogger(__name__)
router = APIRouter()

_JOBS: dict[str, object] = {}


def _load_jobs() -> dict[str, object]:
    global _JOBS
    if not _JOBS:
        from app.jobs import aggregate_allocation, etf_holdings, ishares_holdings, market_cap_enrichment, position_snapshot
        _JOBS = {
            "ishares_holdings":      ishares_holdings.run,
            "etf_holdings":          etf_holdings.run,
            "position_snapshot":     position_snapshot.run,
            "aggregate_allocation":  aggregate_allocation.run,
            "market_cap_enrichment": market_cap_enrichment.run,
        }
    return _JOBS


@router.post("/admin/refresh")
def refresh(job: str, background_tasks: BackgroundTasks) -> dict[str, str]:
    jobs = _load_jobs()
    if job not in jobs:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown job '{job}'. Available: {sorted(jobs)}",
        )
    background_tasks.add_task(jobs[job])  # type: ignore[operator]
    log.info("Enqueued manual refresh for job '%s'", job)
    return {"status": "accepted", "job": job}


@router.post("/admin/backfill")
def backfill_allocation(
    days: int = Query(90, ge=7, le=365, description="Seed daily snapshots going back N days"),
) -> dict:
    """Seed portfolio_allocation_snapshot with synthetic historical data.

    Copies today's snapshot to every day from today-days to yesterday,
    applying a slow random walk (±2 % per step) so segments drift
    realistically over time.  Existing rows for those dates are replaced.
    Useful for testing the drift chart and Δ 30d column before real
    historical data has accumulated.

    Idempotent: safe to re-run.
    """
    import random
    from app.db.reporting import PortfolioAllocationSnapshot
    from app.db.reporting_session import SessionLocal

    today = datetime.date.today()

    session = SessionLocal()
    try:
        today_rows = session.execute(
            select(PortfolioAllocationSnapshot).where(
                PortfolioAllocationSnapshot.as_of_date == today,
            )
        ).scalars().all()

        if not today_rows:
            raise HTTPException(
                status_code=404,
                detail="No allocation snapshot for today — run 'aggregate_allocation' first.",
            )

        # Delete all existing seeded rows in the back-fill window
        cutoff = today - datetime.timedelta(days=days)
        session.query(PortfolioAllocationSnapshot).filter(
            PortfolioAllocationSnapshot.as_of_date >= cutoff,
            PortfolioAllocationSnapshot.as_of_date < today,
        ).delete()

        # Build a per-segment random walk backwards from today.
        # Each day steps ±2 % relative to the previous day's weight.
        rng = random.Random(42)  # fixed seed → reproducible
        # multipliers[segment_key] starts at 1.0, walks back in time
        multipliers: dict[str, float] = {r.segment_key: 1.0 for r in today_rows}

        written = 0
        for offset in range(1, days + 1):
            target_date = today - datetime.timedelta(days=offset)
            # Step each segment's multiplier by ±2%
            for key in multipliers:
                multipliers[key] *= 1.0 + rng.uniform(-0.02, 0.02)
                multipliers[key] = max(0.1, multipliers[key])  # floor at 10% of today

            for r in today_rows:
                m = multipliers[r.segment_key]
                session.add(
                    PortfolioAllocationSnapshot(
                        as_of_date=target_date,
                        dimension=r.dimension,
                        segment_key=r.segment_key,
                        segment_label=r.segment_label,
                        value_eur=round(float(r.value_eur) * m, 2),
                        weight_pct=round(float(r.weight_pct) * m, 6),
                        holding_count=r.holding_count,
                    )
                )
                written += 1

        session.commit()
        log.info("backfill: wrote %d rows across %d days back from %s", written, days, today)
        return {
            "status":      "ok",
            "from_date":   str(cutoff),
            "to_date":     str(today - datetime.timedelta(days=1)),
            "days_seeded": days,
            "rows_written": written,
        }
    except HTTPException:
        raise
    except Exception as exc:
        session.rollback()
        log.exception("backfill: failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        session.close()
