"""Allocation API — stub implementation for M4 frontend development.

The sector breakdown uses representative static weights until the real
aggregation query (position_snapshot JOIN ishares_holding) is wired up
later in M4.  The `stub: true` flag in the response lets the frontend
show a banner while the data is not yet from the database.
"""
from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, Query

log = logging.getLogger(__name__)
router = APIRouter()

# Representative weights from an IWDA-heavy global equity portfolio.
# Replaced by a real DB query once M4 aggregation is complete.
_STUB_SECTORS: list[tuple[str, float]] = [
    ("Technology",             0.222),
    ("Financial Services",     0.162),
    ("Healthcare",             0.112),
    ("Industrials",            0.104),
    ("Consumer Discretionary", 0.103),
    ("Communication Services", 0.083),
    ("Consumer Staples",       0.068),
    ("Energy",                 0.044),
    ("Basic Materials",        0.040),
    ("Real Estate",            0.032),
    ("Utilities",              0.030),
]

_STUB_TOTAL_EUR = 95_000.0  # replaced by real portfolio value once query is wired


@router.get("/allocation")
def get_allocation(
    dimension: str = Query("sector", description="Allocation dimension"),
) -> dict:
    """Return look-through portfolio allocation for the requested dimension.

    Currently a stub — returns representative sector weights.
    ``stub: true`` in the response signals the frontend to show a notice.
    """
    total = _STUB_TOTAL_EUR
    rows = [
        {
            "label": label,
            "value_eur": round(total * weight, 2),
            "weight": round(weight, 4),
        }
        for label, weight in _STUB_SECTORS
    ]
    log.debug("allocation stub: dimension=%s, rows=%d", dimension, len(rows))
    return {
        "as_of_date": str(datetime.date.today()),
        "dimension": dimension,
        "total_eur": round(total, 2),
        "stub": True,
        "rows": rows,
    }
