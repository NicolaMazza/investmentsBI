"""Job: fetch holdings for Vanguard and HSBC ETFs.

Runs all active products whose issuer is 'vanguard' or 'hsbc'.
Products with no source_url are skipped with a warning (rather than
crashing the whole job) so that a missing Vanguard URL does not block
the HSBC fetch, and vice versa.

Manual trigger: POST /api/admin/refresh?job=etf_holdings
"""
from __future__ import annotations

import datetime
import logging

from app.db.reporting import Product, ProductCompositionSnapshot
from app.db.reporting_session import SessionLocal
from app.fetchers.base import get_fetcher, load_all_fetchers
from app.jobs.base import job_context

log = logging.getLogger(__name__)

JOB_NAME = "etf_holdings"
_ISSUERS = ("vanguard", "hsbc")


def run() -> None:
    load_all_fetchers()

    session = SessionLocal()
    try:
        products: list[Product] = (
            session.query(Product)
            .filter(Product.issuer.in_(_ISSUERS), Product.active.is_(True))
            .all()
        )
    finally:
        session.close()

    if not products:
        log.warning("etf_holdings: no active products found for issuers %s", _ISSUERS)
        return

    as_of_date = datetime.date.today()
    total_rows = 0

    with job_context(JOB_NAME) as run_record:
        for product in products:
            if not product.source_url:
                log.warning(
                    "etf_holdings: skipping %s (%s) — source_url is NULL.  "
                    "See fetcher module docstring for instructions.",
                    product.isin,
                    product.name,
                )
                continue

            try:
                fetcher = get_fetcher(product.parser)  # type: ignore[arg-type]
                holdings = fetcher.fetch(product)
            except Exception:
                log.exception(
                    "etf_holdings: fetch failed for %s (%s) — skipping",
                    product.isin,
                    product.name,
                )
                continue

            write_session = SessionLocal()
            try:
                write_session.query(ProductCompositionSnapshot).filter(
                    ProductCompositionSnapshot.as_of_date == as_of_date,
                    ProductCompositionSnapshot.product_isin == product.isin,
                ).delete()

                for h in holdings:
                    write_session.add(
                        ProductCompositionSnapshot(
                            as_of_date=as_of_date,
                            product_isin=product.isin,
                            constituent_isin=h.constituent_isin,
                            constituent_name=h.constituent_name,
                            ticker=h.ticker,
                            weight_pct=h.weight_pct,
                            sector=h.sector,
                            country_listing=h.country_listing,
                            country_incorp=h.country_incorp,
                            native_currency=h.native_currency,
                            asset_class=h.asset_class,
                            market_value_native=h.market_value_native,
                            shares=h.shares,
                        )
                    )
                write_session.commit()
                total_rows += len(holdings)
                log.info("etf_holdings: wrote %d rows for %s", len(holdings), product.isin)
            except Exception:
                write_session.rollback()
                log.exception("etf_holdings: DB write failed for %s", product.isin)
            finally:
                write_session.close()

        run_record.rows_written = total_rows
