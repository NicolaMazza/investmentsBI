"""Drill API — decompose a single segment into held_via + constituents.

GET /api/drill?dimension=sector&segment=Information+Technology&date=2026-05-22

Response
--------
{
  "dimension":  "sector",
  "segment":    "Information Technology",
  "value_eur":  2526.0,
  "weight":     0.193,
  "held_via": [
    {"product_isin": "IE00B4L5Y983", "name": "iShares Core MSCI World",
     "contribution_eur": 2526.0, "contribution_pct": 1.0}
  ],
  "constituents": [                       -- top 25 by contribution
    {"isin": "US0378331005", "name": "Apple Inc",
     "contribution_eur": 235.0, "weight_in_segment": 0.093}
  ]
}

For the `product` dimension, drilling into an ETF shows its top 25
constituents ranked by weight_pct.
"""
from __future__ import annotations

import collections
import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.allocation import (
    _DIMENSION_COLUMNS,
    load_composition_map,
    load_positions,
    resolve_snapshot_date,
)
from app.db.reporting import (
    PositionSnapshot,
    Product,
    ProductCompositionSnapshot,
)
from app.db.reporting_session import get_session

log = logging.getLogger(__name__)
router = APIRouter()

_MAX_CONSTITUENTS = 25


@router.get("/drill")
def get_drill(
    dimension: str = Query("sector", description="Allocation dimension"),
    segment: str = Query(..., description="Segment label to drill into"),
    date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    session: Session = Depends(get_session),
) -> dict:
    """Return held_via and top constituents for a segment."""

    as_of_date: Optional[datetime.date] = None
    if date:
        try:
            as_of_date = datetime.date.fromisoformat(date)
        except ValueError:
            log.warning("drill: invalid date %r, ignoring", date)

    as_of_date = resolve_snapshot_date(session, as_of_date)
    if as_of_date is None:
        raise HTTPException(status_code=404, detail="No position snapshot found")

    positions = load_positions(session, as_of_date)
    if not positions:
        raise HTTPException(status_code=404, detail="No positions for this date")

    total_portfolio_eur = sum(float(p.market_value_eur) for p in positions)  # type: ignore[arg-type]

    # ── product dimension: show ETF's own composition ────────────────────────
    if dimension == "product":
        return _drill_product(session, segment, as_of_date, total_portfolio_eur)

    # ── look-through dimensions ───────────────────────────────────────────────
    label_fn = _DIMENSION_COLUMNS.get(dimension, _DIMENSION_COLUMNS["sector"])

    comp_map = load_composition_map(
        session, {p.product_isin for p in positions}, as_of_date
    )
    product_names = _load_product_names(session)

    held_via: dict[str, float] = collections.defaultdict(float)   # product_isin → eur
    constituents: dict[str, dict] = {}                             # isin → {name, eur}

    for pos in positions:
        mv = float(pos.market_value_eur)  # type: ignore[arg-type]
        rows = comp_map.get(pos.product_isin, [])
        if not rows:
            continue

        weight_sum = sum(float(r.weight_pct) for r in rows)
        if weight_sum <= 0:
            continue

        for r in rows:
            if label_fn(r) != segment:  # type: ignore[operator]
                continue
            contribution = mv * (float(r.weight_pct) / weight_sum)
            held_via[pos.product_isin] += contribution

            key = r.constituent_isin
            if key not in constituents:
                constituents[key] = {
                    "name":             r.constituent_name or key,
                    "contribution_eur": 0.0,
                }
            constituents[key]["contribution_eur"] += contribution

    total_segment_eur = sum(held_via.values())

    held_via_list = sorted(
        [
            {
                "product_isin":     isin,
                "name":             product_names.get(isin, isin),
                "contribution_eur": round(eur, 2),
                "contribution_pct": round(eur / total_segment_eur, 4)
                    if total_segment_eur > 0 else 0.0,
            }
            for isin, eur in held_via.items()
        ],
        key=lambda x: -x["contribution_eur"],
    )

    constituents_list = sorted(
        [
            {
                "isin":              isin,
                "name":              v["name"],
                "contribution_eur":  round(v["contribution_eur"], 2),
                "weight_in_segment": round(v["contribution_eur"] / total_segment_eur, 4)
                    if total_segment_eur > 0 else 0.0,
            }
            for isin, v in constituents.items()
        ],
        key=lambda x: -x["contribution_eur"],
    )[:_MAX_CONSTITUENTS]

    return {
        "dimension":    dimension,
        "segment":      segment,
        "value_eur":    round(total_segment_eur, 2),
        "weight":       round(total_segment_eur / total_portfolio_eur, 4)
            if total_portfolio_eur > 0 else 0.0,
        "held_via":     held_via_list,
        "constituents": constituents_list,
    }


# ── product dimension drill ───────────────────────────────────────────────────

def _drill_product(
    session: Session,
    product_name: str,
    as_of_date: datetime.date,
    total_portfolio_eur: float,
) -> dict:
    """Drill into one ETF: show its top constituents by weight."""
    product = session.execute(
        select(Product).where(Product.name == product_name)
    ).scalar_one_or_none()

    if product is None:
        raise HTTPException(status_code=404, detail=f"Product not found: {product_name}")

    latest_comp_date = session.execute(
        select(func.max(ProductCompositionSnapshot.as_of_date)).where(
            ProductCompositionSnapshot.product_isin == product.isin,
            ProductCompositionSnapshot.as_of_date <= as_of_date,
        )
    ).scalar()

    pos = session.execute(
        select(PositionSnapshot).where(
            PositionSnapshot.as_of_date == as_of_date,
            PositionSnapshot.product_isin == product.isin,
        )
    ).scalar_one_or_none()
    product_mv = float(pos.market_value_eur) if (pos and pos.market_value_eur) else 0.0

    if latest_comp_date is None:
        return {
            "dimension":    "product",
            "segment":      product_name,
            "value_eur":    round(product_mv, 2),
            "weight":       round(product_mv / total_portfolio_eur, 4)
                if total_portfolio_eur > 0 else 0.0,
            "held_via":     [{"product_isin": product.isin, "name": product.name,
                               "contribution_eur": round(product_mv, 2),
                               "contribution_pct": 1.0}],
            "constituents": [],
        }

    comp_rows = session.execute(
        select(ProductCompositionSnapshot).where(
            ProductCompositionSnapshot.product_isin == product.isin,
            ProductCompositionSnapshot.as_of_date == latest_comp_date,
        ).order_by(ProductCompositionSnapshot.weight_pct.desc())
    ).scalars().all()

    weight_sum = sum(float(r.weight_pct) for r in comp_rows) or 1.0

    constituents_list = [
        {
            "isin":              r.constituent_isin,
            "name":              r.constituent_name or r.constituent_isin,
            "contribution_eur":  round(product_mv * float(r.weight_pct) / weight_sum, 2),
            "weight_in_segment": round(float(r.weight_pct) / weight_sum, 4),
        }
        for r in comp_rows[:_MAX_CONSTITUENTS]
    ]

    return {
        "dimension":    "product",
        "segment":      product_name,
        "value_eur":    round(product_mv, 2),
        "weight":       round(product_mv / total_portfolio_eur, 4)
            if total_portfolio_eur > 0 else 0.0,
        "held_via":     [{"product_isin": product.isin, "name": product.name,
                           "contribution_eur": round(product_mv, 2),
                           "contribution_pct": 1.0}],
        "constituents": constituents_list,
    }


def _load_product_names(session: Session) -> dict[str, str]:
    return {
        p.isin: p.name
        for p in session.execute(select(Product)).scalars().all()
    }
