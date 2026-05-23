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
        from app.jobs import aggregate_allocation, etf_holdings, ishares_holdings, position_snapshot
        _JOBS = {
            "ishares_holdings":     ishares_holdings.run,
            "etf_holdings":         etf_holdings.run,
            "position_snapshot":    position_snapshot.run,
            "aggregate_allocation": aggregate_allocation.run,
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
    days: int = Query(32, ge=1, le=365, description="Seed a snapshot N days in the past"),
) -> dict:
    """Copy today's portfolio_allocation_snapshot to today-N days.

    Used to generate historical data for testing the Δ 30d column.
    The copied rows get a small synthetic ±noise on weight_pct so the
    delta is non-zero and visually interesting.
    Idempotent: re-running for the same offset overwrites the previous seed.
    """
    import random
    from app.db.reporting import PortfolioAllocationSnapshot
    from app.db.reporting_session import SessionLocal

    today = datetime.date.today()
    target_date = today - datetime.timedelta(days=days)

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

        # Delete any existing rows for target_date
        session.query(PortfolioAllocationSnapshot).filter(
            PortfolioAllocationSnapshot.as_of_date == target_date,
        ).delete()

        rng = random.Random(int(target_date.strftime("%Y%m%d")))  # deterministic seed
        written = 0
        for r in today_rows:
            # Apply ±15% relative noise to weight and value so deltas look realistic
            noise = 1.0 + rng.uniform(-0.15, 0.15)
            new_weight = max(0.0, float(r.weight_pct) * noise)
            new_value  = max(0.0, float(r.value_eur)  * noise)
            session.add(
                PortfolioAllocationSnapshot(
                    as_of_date=target_date,
                    dimension=r.dimension,
                    segment_key=r.segment_key,
                    segment_label=r.segment_label,
                    value_eur=round(new_value, 2),
                    weight_pct=round(new_weight, 6),
                    holding_count=r.holding_count,
                )
            )
            written += 1

        session.commit()
        log.info("backfill: wrote %d rows for %s (today-%d)", written, target_date, days)
        return {
            "status":      "ok",
            "target_date": str(target_date),
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
