"""Allocation API — look-through aggregation across all supported dimensions.

Supported dimensions
--------------------
sector       : GICS sector from composition (treemap)
country      : country_listing from composition (treemap)
currency     : native_currency from composition (donut)
company      : constituent name (top 30 + Other, treemap)
product      : one bucket per ETF held, no look-through (treemap)
market_cap   : instrument_reference.market_cap_bucket — disabled until M7
               enrichment job runs; returns stub "Unknown" for all.

Pipeline (sector / country / currency / company)
-------------------------------------------------
1. Resolve as-of date from position_snapshot.
2. Load positions for that date with market_value_eur > 0.
3. For each position ISIN, find the latest product_composition_snapshot.
4. Distribute market_value_eur across dimension buckets by normalised weight_pct.
5. Positions with no composition → "Other" bucket.
6. Falls back to a representative stub when the DB has no position rows.

Pipeline (product)
------------------
Return positions directly, labelled by product.name — no look-through.
"""
from __future__ import annotations

import collections
import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.reporting import (
    PositionSnapshot,
    Product,
    ProductCompositionSnapshot,
)
from app.db.reporting_session import get_session

log = logging.getLogger(__name__)
router = APIRouter()

_TOP_N_COMPANY = 30  # cap for company dimension to keep treemap readable

# ── Dimension helpers ─────────────────────────────────────────────────────────

_DIMENSION_COLUMNS: dict[str, object] = {
    "sector":   lambda r: r.sector          or "Unknown",
    "country":  lambda r: r.country_listing or r.country_incorp or "Unknown",
    "currency": lambda r: r.native_currency or "Unknown",
    "company":  lambda r: r.constituent_name or r.constituent_isin or "Unknown",
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
        "as_of_date":   str(datetime.date.today()),
        "dimension":    dimension,
        "total_eur":    round(total, 2),
        "funds":        1,
        "look_through": None,
        "top_single":   None,
        "stub":         True,
        "rows":         rows,
    }


# ── Helpers shared with drill.py ──────────────────────────────────────────────

def resolve_snapshot_date(
    session: Session,
    requested: Optional[datetime.date],
) -> Optional[datetime.date]:
    """Return the requested date if given, otherwise the latest position date."""
    if requested is not None:
        return requested
    return session.execute(
        select(func.max(PositionSnapshot.as_of_date))
    ).scalar()


def load_positions(
    session: Session,
    as_of_date: datetime.date,
) -> list[PositionSnapshot]:
    return session.execute(
        select(PositionSnapshot).where(
            PositionSnapshot.as_of_date == as_of_date,
            PositionSnapshot.market_value_eur.isnot(None),
            PositionSnapshot.market_value_eur > 0,
        )
    ).scalars().all()


def load_composition_map(
    session: Session,
    isins: set[str],
    as_of_date: datetime.date,
) -> dict[str, list[ProductCompositionSnapshot]]:
    """Return latest composition rows per product ISIN, on or before as_of_date."""
    comp_rows: dict[str, list[ProductCompositionSnapshot]] = {}
    for isin in isins:
        d = session.execute(
            select(func.max(ProductCompositionSnapshot.as_of_date)).where(
                ProductCompositionSnapshot.product_isin == isin,
                ProductCompositionSnapshot.as_of_date <= as_of_date,
            )
        ).scalar()
        if d is None:
            continue
        rows = session.execute(
            select(ProductCompositionSnapshot).where(
                ProductCompositionSnapshot.product_isin == isin,
                ProductCompositionSnapshot.as_of_date == d,
            )
        ).scalars().all()
        comp_rows[isin] = rows
    return comp_rows


# ── Aggregation logic ─────────────────────────────────────────────────────────

def _query_allocation(
    session: Session,
    dimension: str,
    as_of_date: Optional[datetime.date],
) -> Optional[dict]:
    """Return real look-through allocation from the reporting DB, or None."""

    as_of_date = resolve_snapshot_date(session, as_of_date)
    if as_of_date is None:
        return None

    positions = load_positions(session, as_of_date)
    if not positions:
        return None

    total_eur = sum(float(p.market_value_eur) for p in positions)  # type: ignore[arg-type]
    if total_eur <= 0:
        return None

    # ── product dimension: no look-through ───────────────────────────────────
    if dimension == "product":
        product_names: dict[str, str] = {
            p.isin: p.name
            for p in session.execute(select(Product)).scalars().all()
        }
        buckets: dict[str, float] = collections.defaultdict(float)
        for pos in positions:
            label = product_names.get(pos.product_isin, pos.product_isin)
            buckets[label] += float(pos.market_value_eur)  # type: ignore[arg-type]

        rows = [
            {"label": lbl, "value_eur": round(v, 2), "weight": round(v / total_eur, 4)}
            for lbl, v in sorted(buckets.items(), key=lambda x: -x[1])
        ]
        return {
            "as_of_date":   str(as_of_date),
            "dimension":    dimension,
            "total_eur":    round(total_eur, 2),
            "funds":        len(positions),
            "look_through": None,
            "top_single":   None,
            "stub":         False,
            "rows":         rows,
        }

    # ── look-through dimensions ───────────────────────────────────────────────
    label_fn = _DIMENSION_COLUMNS.get(dimension, _DIMENSION_COLUMNS["sector"])

    comp_map = load_composition_map(
        session, {p.product_isin for p in positions}, as_of_date
    )

    buckets_lt: dict[str, float] = collections.defaultdict(float)
    for pos in positions:
        mv = float(pos.market_value_eur)  # type: ignore[arg-type]
        rows_c = comp_map.get(pos.product_isin, [])
        if not rows_c:
            buckets_lt["Other"] += mv
            continue
        weight_sum = sum(float(r.weight_pct) for r in rows_c)
        if weight_sum <= 0:
            buckets_lt["Other"] += mv
            continue
        for r in rows_c:
            buckets_lt[label_fn(r)] += mv * (float(r.weight_pct) / weight_sum)  # type: ignore[operator]

    # Cap company dimension to top _TOP_N_COMPANY
    if dimension == "company":
        sorted_b = sorted(buckets_lt.items(), key=lambda x: -x[1])
        top = sorted_b[:_TOP_N_COMPANY]
        rest_val = sum(v for _, v in sorted_b[_TOP_N_COMPANY:])
        rest_cnt = len(sorted_b) - _TOP_N_COMPANY
        if rest_val > 0.01:
            top.append((f"Other ({rest_cnt} more)", rest_val))
        sorted_buckets = top
    else:
        sorted_buckets = sorted(buckets_lt.items(), key=lambda x: -x[1])

    response_rows = [
        {"label": lbl, "value_eur": round(v, 2), "weight": round(v / total_eur, 4)}
        for lbl, v in sorted_buckets
        if v > 0.01
    ]

    return {
        "as_of_date":   str(as_of_date),
        "dimension":    dimension,
        "total_eur":    round(total_eur, 2),
        "funds":        len(positions),
        "look_through": None,
        "top_single":   None,
        "stub":         False,
        "rows":         response_rows,
    }


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("/allocation")
def get_allocation(
    dimension: str = Query("sector", description="Allocation dimension"),
    date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD; defaults to latest snapshot"),
    session: Session = Depends(get_session),
) -> dict:
    """Return look-through portfolio allocation for the requested dimension."""
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
