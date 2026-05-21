from __future__ import annotations

import datetime
import logging

from app.db.reporting import Product, ProductCompositionSnapshot
from app.db.reporting_session import SessionLocal
from app.fetchers.base import get_fetcher, load_all_fetchers
from app.jobs.base import job_context

log = logging.getLogger(__name__)

JOB_NAME = "ishares_holdings"


def run() -> None:
    load_all_fetchers()
    session = SessionLocal()
    try:
        products = (
            session.query(Product)
            .filter(Product.issuer == "ishares", Product.active.is_(True))
            .all()
        )
    finally:
        session.close()

    as_of_date = datetime.date.today()
    total_rows = 0

    with job_context(JOB_NAME) as run_record:
        for product in products:
            fetcher = get_fetcher(product.parser)  # type: ignore[arg-type]
            holdings = fetcher.fetch(product)

            write_session = SessionLocal()
            try:
                # Remove existing rows for this product+date before inserting
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
                log.info("Wrote %d rows for %s", len(holdings), product.isin)
            except Exception:
                write_session.rollback()
                raise
            finally:
                write_session.close()

        run_record.rows_written = total_rows
