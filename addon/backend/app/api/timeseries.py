"""Timeseries API — historical weight% per dimension segment.

Returns portfolio_allocation_snapshot rows for the last N days, pivoted
into a chart-friendly format: one date array + one series per segment.

Only segments that exist in the *latest* available snapshot are returned,
ordered by descending weight so the most important ones come first.
Segments beyond the top-8 cap are merged into "Other" to keep the chart
readable.

GET /api/timeseries?dimension=sector&days=90
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.reporting import PortfolioAllocationSnapshot
from app.db.reporting_session import get_session

log = logging.getLogger(__name__)
router = APIRouter()

_MAX_SERIES = 8  # segments beyond this are collapsed to "Other"


@router.get("/timeseries")
def get_timeseries(
    dimension: str = Query("sector"),
    days: int = Query(90, ge=7, le=365),
    session: Session = Depends(get_session),
) -> dict:
    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=days)

    # All snapshot rows for this dimension in the window
    rows = session.execute(
        select(PortfolioAllocationSnapshot).where(
            PortfolioAllocationSnapshot.dimension == dimension,
            PortfolioAllocationSnapshot.as_of_date >= cutoff,
        ).order_by(PortfolioAllocationSnapshot.as_of_date)
    ).scalars().all()

    if not rows:
        return {"dimension": dimension, "days": days, "dates": [], "series": []}

    # Determine the top-N segments by weight in the latest date
    latest_date = max(r.as_of_date for r in rows)
    latest_rows = [r for r in rows if r.as_of_date == latest_date]
    top_segments: list[str] = [
        r.segment_label
        for r in sorted(latest_rows, key=lambda r: float(r.weight_pct), reverse=True)
    ][:_MAX_SERIES]

    # Collect all unique dates in the window, sorted
    dates: list[datetime.date] = sorted({r.as_of_date for r in rows})

    # Build a lookup: (date, segment_label) → weight_pct
    lookup: dict[tuple[datetime.date, str], float] = {
        (r.as_of_date, r.segment_label): float(r.weight_pct) * 100
        for r in rows
    }

    # Build series for top segments
    series: list[dict] = []
    for label in top_segments:
        data = [
            round(lookup.get((d, label), 0.0), 3)
            for d in dates
        ]
        series.append({"label": label, "data": data})

    # "Other" = sum of all non-top segments per date
    other_per_date: dict[datetime.date, float] = {}
    for r in rows:
        if r.segment_label not in top_segments:
            other_per_date[r.as_of_date] = (
                other_per_date.get(r.as_of_date, 0.0) + float(r.weight_pct) * 100
            )
    if any(v > 0.01 for v in other_per_date.values()):
        series.append({
            "label": "Other",
            "data": [round(other_per_date.get(d, 0.0), 3) for d in dates],
        })

    log.debug(
        "timeseries: dimension=%s days=%d dates=%d series=%d",
        dimension, days, len(dates), len(series),
    )
    return {
        "dimension": dimension,
        "days":      days,
        "dates":     [str(d) for d in dates],
        "series":    series,
    }
