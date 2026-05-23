"""Job: pre-compute portfolio_allocation_snapshot for all dimensions.

Runs nightly (20 min after etf_holdings) and after any manual job chain.
Reads today's position_snapshot + latest product_composition_snapshot rows,
distributes market_value_eur across every dimension bucket, and writes one
row per (date, dimension, segment_key) to portfolio_allocation_snapshot.

This makes the /api/allocation endpoint read pre-computed rows instead of
doing the heavy join on every page load, and provides the historical data
that powers the Δ 30d column in the data table.

Manual trigger: POST /api/admin/refresh?job=aggregate_allocation
"""
from __future__ import annotations

import collections
import datetime
import logging

from sqlalchemy import select

from app.api.allocation import load_composition_map, load_instrument_reference, load_positions, resolve_snapshot_date
from app.db.reporting import PortfolioAllocationSnapshot
from app.db.reporting_session import SessionLocal
from app.jobs.base import job_context

log = logging.getLogger(__name__)

JOB_NAME = "aggregate_allocation"

_DIMENSIONS = ("sector", "country", "currency", "company", "product", "market_cap")

_LABEL_FNS: dict[str, object] = {
    "sector":     lambda r: r.sector          or "Unknown",
    "country":    lambda r: r.country_listing or r.country_incorp or "Unknown",
    "currency":   lambda r: r.native_currency or "Unknown",
    "company":    lambda r: r.constituent_name or r.constituent_isin or "Unknown",
    "market_cap": lambda r: getattr(r, "market_cap_bucket", None) or "Unknown",
}


def run() -> None:
    as_of_date = datetime.date.today()
    total_rows = 0

    with job_context(JOB_NAME) as run_record:
        session = SessionLocal()
        try:
            positions = load_positions(session, as_of_date)
            if not positions:
                log.warning("aggregate_allocation: no positions for %s — skipping", as_of_date)
                return

            total_eur = sum(float(p.market_value_eur) for p in positions)  # type: ignore[arg-type]
            if total_eur <= 0:
                log.warning("aggregate_allocation: total_eur=0 for %s — skipping", as_of_date)
                return

            comp_map = load_composition_map(
                session, {p.product_isin for p in positions}, as_of_date
            )

            # ── product dimension ─────────────────────────────────────────────
            from app.db.reporting import Product
            product_names: dict[str, str] = {
                p.isin: p.name
                for p in session.execute(select(Product)).scalars().all()
            }
            product_buckets: dict[str, float] = collections.defaultdict(float)
            product_counts: dict[str, set[str]] = collections.defaultdict(set)
            for pos in positions:
                lbl = product_names.get(pos.product_isin, pos.product_isin)
                product_buckets[lbl] += float(pos.market_value_eur)  # type: ignore[arg-type]
                product_counts[lbl].add(pos.product_isin)

            all_buckets: dict[str, dict[str, float]] = {"product": dict(product_buckets)}
            all_counts:  dict[str, dict[str, int]]   = {
                "product": {k: len(v) for k, v in product_counts.items()}
            }

            # Attach market_cap_bucket for the market_cap dimension
            ref_map = load_instrument_reference(session)
            for rows_c in comp_map.values():
                for r in rows_c:
                    r.market_cap_bucket = ref_map.get(r.constituent_isin)  # type: ignore[attr-defined]

            # ── look-through dimensions ───────────────────────────────────────
            for dim in ("sector", "country", "currency", "company", "market_cap"):
                label_fn = _LABEL_FNS[dim]  # type: ignore[index]
                buckets: dict[str, float] = collections.defaultdict(float)
                counts:  dict[str, int]   = collections.defaultdict(int)

                for pos in positions:
                    mv = float(pos.market_value_eur)  # type: ignore[arg-type]
                    rows_c = comp_map.get(pos.product_isin, [])
                    if not rows_c:
                        buckets["Other"] += mv
                        counts["Other"]  += 1
                        continue
                    weight_sum = sum(float(r.weight_pct) for r in rows_c)
                    if weight_sum <= 0:
                        buckets["Other"] += mv
                        counts["Other"]  += 1
                        continue
                    for r in rows_c:
                        lbl = label_fn(r)  # type: ignore[operator]
                        buckets[lbl] += mv * (float(r.weight_pct) / weight_sum)
                        counts[lbl]  += 1

                all_buckets[dim] = dict(buckets)
                all_counts[dim]  = dict(counts)

        finally:
            session.close()

        # ── Write ─────────────────────────────────────────────────────────────
        write_session = SessionLocal()
        try:
            # Delete today's existing rows for all dimensions.
            write_session.query(PortfolioAllocationSnapshot).filter(
                PortfolioAllocationSnapshot.as_of_date == as_of_date,
            ).delete()

            for dim in _DIMENSIONS:
                buckets = all_buckets.get(dim, {})
                counts  = all_counts.get(dim, {})
                for label, value in buckets.items():
                    if value < 0.01:
                        continue
                    write_session.add(
                        PortfolioAllocationSnapshot(
                            as_of_date=as_of_date,
                            dimension=dim,
                            segment_key=label,
                            segment_label=label,
                            value_eur=round(value, 2),
                            weight_pct=round(value / total_eur, 6),
                            holding_count=counts.get(label, 0),
                        )
                    )
                    total_rows += 1

            write_session.commit()
            log.info(
                "aggregate_allocation: wrote %d rows for %s (total_eur=%.0f)",
                total_rows, as_of_date, total_eur,
            )
        except Exception:
            write_session.rollback()
            log.exception("aggregate_allocation: DB write failed")
            raise
        finally:
            write_session.close()

        run_record.rows_written = total_rows
