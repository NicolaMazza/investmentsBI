"""Allocation API — look-through sector/country/currency aggregation.

Pipeline:
  1. Resolve the as-of date (latest position_snapshot date, or the requested date).
  2. Load all positions for that date with market_value_eur > 0.
  3. For each position, find the latest product_composition_snapshot for its ISIN.
  4. Distribute each position's market_value_eur across dimension buckets weighted
     by weight_pct (normalised so they sum to 1.0 within each composition).
  5. Positions with no composition data fall into an "Other" bucket.
  6. Falls back to a representative stub when the DB has no position rows.
"""
from __future__ import annotations

import collections
import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.reporting import PositionSnapshot, ProductCompositionSnapshot
from app.db.reporting_session import get_session

log = logging.getLogger(__name__)
router = APIRouter()

# ── Dimension helpers ─────────────────────────────────────────────────────────

_DIMENSION_COLUMNS = {
    "sector":   lambda r: r.sector          or "Unknown",
    "country":  lambda r: r.country_listing or r.country_incorp or "Unknown",
    "currency": lambda r: r.native_currency or "Unknown",
}

# ── Stub fallback ─────────────────────────────────────────────────────────────

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
_STUB_TOTAL_EUR = 95_000.0


def _stub_response(dimension: str) -> dict:
    total = _STUB_TOTAL_EUR
    rows = [
        {"label": lbl, "value_eur": round(total * w, 2), "weight": round(w, 4)}
        for lbl, w in _STUB_SECTORS
    ]
    return {
        "as_of_date": str(datetime.date.today()),
        "dimension": dimension,
        "total_eur": round(total, 2),
        "funds": 1,
        "look_through": None,
        "top_single": None,
        "stub": True,
        "rows": rows,
    }


# ── Aggregation logic ─────────────────────────────────────────────────────────

def _query_allocation(
    session: Session,
    dimension: str,
    as_of_date: Optional[datetime.date] = None,
) -> dict:
    """Return real look-through allocation from the reporting DB."""

    label_fn = _DIMENSION_COLUMNS.get(dimension, _DIMENSION_COLUMNS["sector"])

    # 1. Resolve snapshot date
    if as_of_date is None:
        row = session.execute(
            select(func.max(PositionSnapshot.as_of_date))
        ).scalar()
        if row is None:
            return None  # caller will fall back to stub
        as_of_date = row

    # 2. Load positions for that date
    positions = session.execute(
        select(PositionSnapshot).where(
            PositionSnapshot.as_of_date == as_of_date,
            PositionSnapshot.market_value_eur.isnot(None),
            PositionSnapshot.market_value_eur > 0,
        )
    ).scalars().all()

    if not positions:
        return None

    # 3. For each unique ISIN in the portfolio, find the latest composition date
    unique_isins = {p.isin for p in positions if p.isin}
    latest_comp_date: dict[str, datetime.date] = {}
    for isin in unique_isins:
        d = session.execute(
            select(func.max(ProductCompositionSnapshot.as_of_date)).where(
                ProductCompositionSnapshot.product_isin == isin,
                ProductCompositionSnapshot.as_of_date <= as_of_date,
            )
        ).scalar()
        if d is not None:
            latest_comp_date[isin] = d

    # 4. Load composition rows (bulk, one query per ISIN × date pair)
    comp_rows: dict[str, list[ProductCompositionSnapshot]] = collections.defaultdict(list)
    for isin, d in latest_comp_date.items():
        rows = session.execute(
            select(ProductCompositionSnapshot).where(
                ProductCompositionSnapshot.product_isin == isin,
                ProductCompositionSnapshot.as_of_date == d,
            )
        ).scalars().all()
        comp_rows[isin] = rows

    # 5. Distribute market_value_eur across dimension buckets
    buckets: dict[str, float] = collections.defaultdict(float)
    total_eur = 0.0

    for pos in positions:
        mv = float(pos.market_value_eur)
        total_eur += mv
        rows = comp_rows.get(pos.isin or "", [])

        if not rows:
            # No look-through data — attribute to "Other"
            buckets["Other"] += mv
            continue

        # Normalise weights (iShares CSV weights can sum to <100 due to rounding)
        weight_sum = sum(float(r.weight_pct) for r in rows)
        if weight_sum <= 0:
            buckets["Other"] += mv
            continue

        for r in rows:
            label = label_fn(r)
            buckets[label] += mv * (float(r.weight_pct) / weight_sum)

    # 6. Build sorted response rows
    if total_eur <= 0:
        return None

    sorted_buckets = sorted(buckets.items(), key=lambda x: x[1], reverse=True)
    response_rows = [
        {
            "label":     label,
            "value_eur": round(value, 2),
            "weight":    round(value / total_eur, 4),
        }
        for label, value in sorted_buckets
        if value > 0.01  # drop sub-cent noise
    ]

    return {
        "as_of_date":   str(as_of_date),
        "dimension":    dimension,
        "total_eur":    round(total_eur, 2),
        "funds":        len(positions),
        "look_through": None,   # future milestone
        "top_single":   None,   # future milestone
        "stub":         False,
        "rows":         response_rows,
    }


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("/allocation")
def get_allocation(
    dimension: str = Query("sector", description="Allocation dimension"),
    date: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD); defaults to latest snapshot"),
    session: Session = Depends(get_session),
) -> dict:
    """Return look-through portfolio allocation for the requested dimension.

    Falls back to a representative stub when no position data is available,
    signalled by ``stub: true`` in the response.
    """
    as_of_date: Optional[datetime.date] = None
    if date:
        try:
            as_of_date = datetime.date.fromisoformat(date)
        except ValueError:
            log.warning("allocation: invalid date param %r, ignoring", date)

    try:
        result = _query_allocation(session, dimension, as_of_date)
    except Exception:
        log.exception("allocation: DB query failed, falling back to stub")
        result = None

    if result is None:
        log.debug("allocation: no DB data, returning stub")
        return _stub_response(dimension)

    log.debug(
        "allocation: dimension=%s date=%s rows=%d total=%.2f",
        dimension, result["as_of_date"], len(result["rows"]), result["total_eur"],
    )
    return result
