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

Read path
---------
1. If portfolio_allocation_snapshot has rows for the requested date, serve
   them directly (fast path — written nightly by aggregate_allocation job).
2. Otherwise fall back to on-the-fly computation from raw position and
   composition tables (slow path — used on first run before any cron fires).

KPIs
----
look_through  : % of portfolio EUR covered by composition data (0–100)
top_single    : label of the heaviest single constituent (company dimension)
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
    InstrumentReference,
    PortfolioAllocationSnapshot,
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
    "sector":     lambda r: r.sector          or "Unknown",
    "country":    lambda r: r.country_listing or r.country_incorp or "Unknown",
    "currency":   lambda r: r.native_currency or "Unknown",
    "company":    lambda r: r.constituent_name or r.constituent_isin or "Unknown",
    "market_cap": lambda r: r.market_cap_bucket or "Unknown",  # joined in below
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
        {"label": lbl, "value_eur": round(total * w, 2), "weight": round(w, 4), "delta_30d": None}
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


def load_instrument_reference(
    session: Session,
) -> dict[str, str]:
    """Return {constituent_isin: market_cap_bucket} for all known instruments."""
    rows = session.execute(
        select(InstrumentReference).where(
            InstrumentReference.market_cap_bucket.isnot(None)
        )
    ).scalars().all()
    return {r.isin: r.market_cap_bucket for r in rows}  # type: ignore[return-value]


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


# ── KPI helpers ───────────────────────────────────────────────────────────────

def _compute_look_through(
    positions: list[PositionSnapshot],
    comp_map: dict[str, list[ProductCompositionSnapshot]],
    total_eur: float,
) -> Optional[str]:
    """Return look-through coverage as a '%.0f%%' string, or None."""
    if total_eur <= 0:
        return None
    covered = sum(
        float(p.market_value_eur)  # type: ignore[arg-type]
        for p in positions
        if comp_map.get(p.product_isin)
    )
    pct = covered / total_eur * 100
    return f"{pct:.0f}%"


def _compute_top_single(
    positions: list[PositionSnapshot],
    comp_map: dict[str, list[ProductCompositionSnapshot]],
    total_eur: float,
) -> Optional[str]:
    """Return '<name> <weight%>' for the heaviest single constituent."""
    if total_eur <= 0:
        return None
    buckets: dict[str, float] = collections.defaultdict(float)
    for pos in positions:
        mv = float(pos.market_value_eur)  # type: ignore[arg-type]
        rows_c = comp_map.get(pos.product_isin, [])
        if not rows_c:
            continue
        weight_sum = sum(float(r.weight_pct) for r in rows_c)
        if weight_sum <= 0:
            continue
        for r in rows_c:
            name = r.constituent_name or r.constituent_isin or "Unknown"
            buckets[name] += mv * (float(r.weight_pct) / weight_sum)
    if not buckets:
        return None
    top_name, top_val = max(buckets.items(), key=lambda x: x[1])
    return f"{top_name} {top_val / total_eur * 100:.1f}%"


# ── Delta helpers ─────────────────────────────────────────────────────────────

def _load_delta_map(
    session: Session,
    dimension: str,
    as_of_date: datetime.date,
    days: int = 30,
) -> dict[str, float]:
    """Return {segment_key: weight_pct} from ~`days` ago, or {} if unavailable."""
    target = as_of_date - datetime.timedelta(days=days)
    # Find closest available date on or before target.
    prev_date = session.execute(
        select(func.max(PortfolioAllocationSnapshot.as_of_date)).where(
            PortfolioAllocationSnapshot.dimension == dimension,
            PortfolioAllocationSnapshot.as_of_date <= target,
        )
    ).scalar()
    if prev_date is None:
        return {}
    rows = session.execute(
        select(PortfolioAllocationSnapshot).where(
            PortfolioAllocationSnapshot.as_of_date == prev_date,
            PortfolioAllocationSnapshot.dimension == dimension,
        )
    ).scalars().all()
    return {r.segment_key: float(r.weight_pct) for r in rows}


# ── Pre-computed fast path ────────────────────────────────────────────────────

def _query_from_precomputed(
    session: Session,
    dimension: str,
    as_of_date: datetime.date,
) -> Optional[dict]:
    """Serve from portfolio_allocation_snapshot if available for this date."""
    rows = session.execute(
        select(PortfolioAllocationSnapshot).where(
            PortfolioAllocationSnapshot.as_of_date == as_of_date,
            PortfolioAllocationSnapshot.dimension == dimension,
        ).order_by(PortfolioAllocationSnapshot.value_eur.desc())
    ).scalars().all()

    if not rows:
        return None

    total_eur = sum(float(r.value_eur) for r in rows)
    delta_map = _load_delta_map(session, dimension, as_of_date)

    response_rows = []
    for r in rows:
        w_now  = float(r.weight_pct)
        w_prev = delta_map.get(r.segment_key)
        delta  = round((w_now - w_prev) * 100, 2) if w_prev is not None else None
        response_rows.append({
            "label":     r.segment_label,
            "value_eur": float(r.value_eur),
            "weight":    round(w_now, 4),
            "delta_30d": delta,
        })

    # Cap company to top N + Other
    if dimension == "company" and len(response_rows) > _TOP_N_COMPANY:
        top   = response_rows[:_TOP_N_COMPANY]
        rest  = response_rows[_TOP_N_COMPANY:]
        rest_val = sum(r["value_eur"] for r in rest)
        if rest_val > 0.01:
            top.append({
                "label":     f"Other ({len(rest)} more)",
                "value_eur": round(rest_val, 2),
                "weight":    round(rest_val / total_eur, 4),
                "delta_30d": None,
            })
        response_rows = top

    # KPIs: read from positions for look_through + top_single.
    positions  = load_positions(session, as_of_date)
    comp_map   = load_composition_map(session, {p.product_isin for p in positions}, as_of_date)
    look_through = _compute_look_through(positions, comp_map, total_eur)
    top_single   = _compute_top_single(positions, comp_map, total_eur)

    return {
        "as_of_date":   str(as_of_date),
        "dimension":    dimension,
        "total_eur":    round(total_eur, 2),
        "funds":        len(positions),
        "look_through": look_through,
        "top_single":   top_single,
        "stub":         False,
        "rows":         response_rows,
    }


# ── Aggregation logic (slow / on-the-fly path) ───────────────────────────────

def _query_allocation(
    session: Session,
    dimension: str,
    as_of_date: Optional[datetime.date],
) -> Optional[dict]:
    """Return real look-through allocation from the reporting DB, or None."""

    as_of_date = resolve_snapshot_date(session, as_of_date)
    if as_of_date is None:
        return None

    # Fast path: pre-computed rows exist.
    precomputed = _query_from_precomputed(session, dimension, as_of_date)
    if precomputed is not None:
        log.debug("allocation: served from pre-computed snapshot (%s, %s)", dimension, as_of_date)
        return precomputed

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
        buckets_p: dict[str, float] = collections.defaultdict(float)
        for pos in positions:
            label = product_names.get(pos.product_isin, pos.product_isin)
            buckets_p[label] += float(pos.market_value_eur)  # type: ignore[arg-type]

        rows_p = [
            {"label": lbl, "value_eur": round(v, 2), "weight": round(v / total_eur, 4), "delta_30d": None}
            for lbl, v in sorted(buckets_p.items(), key=lambda x: -x[1])
        ]
        return {
            "as_of_date":   str(as_of_date),
            "dimension":    dimension,
            "total_eur":    round(total_eur, 2),
            "funds":        len(positions),
            "look_through": None,
            "top_single":   None,
            "stub":         False,
            "rows":         rows_p,
        }

    # ── look-through dimensions ───────────────────────────────────────────────
    label_fn = _DIMENSION_COLUMNS.get(dimension, _DIMENSION_COLUMNS["sector"])

    comp_map = load_composition_map(
        session, {p.product_isin for p in positions}, as_of_date
    )

    # Attach market_cap_bucket to each composition row (needed for market_cap dim).
    if dimension == "market_cap":
        ref_map = load_instrument_reference(session)
        for rows_c in comp_map.values():
            for r in rows_c:
                r.market_cap_bucket = ref_map.get(r.constituent_isin)  # type: ignore[attr-defined]

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

    look_through = _compute_look_through(positions, comp_map, total_eur)
    top_single   = _compute_top_single(positions, comp_map, total_eur)

    response_rows = [
        {"label": lbl, "value_eur": round(v, 2), "weight": round(v / total_eur, 4), "delta_30d": None}
        for lbl, v in sorted_buckets
        if v > 0.01
    ]

    return {
        "as_of_date":   str(as_of_date),
        "dimension":    dimension,
        "total_eur":    round(total_eur, 2),
        "funds":        len(positions),
        "look_through": look_through,
        "top_single":   top_single,
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
