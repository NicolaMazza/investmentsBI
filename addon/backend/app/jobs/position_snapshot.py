"""position_snapshot job.

Steps
-----
1. Fetch ECB FX rates for today and upsert into fx_rate.
2. Build position snapshot rows via the aggregator.
3. Delete existing rows for today and insert the new snapshot.
"""
from __future__ import annotations

import datetime
import logging

from app.aggregator.position_snapshot import build_position_snapshot
from app.config import settings
from app.db.reporting import FxRate, PositionSnapshot
from app.db.reporting_session import SessionLocal as ReportingSession
from app.db.ghostfolio_session import SessionLocal as GhostfolioSession
from app.fetchers.ecb_fx import fetch_ecb_rates
from app.jobs.base import job_context

log = logging.getLogger(__name__)

JOB_NAME = "position_snapshot"


def run() -> None:
    as_of_date = datetime.date.today()

    with job_context(JOB_NAME) as run_record:
        # ---- Step 1: ECB FX rates ----------------------------------------
        ecb_date, rates = fetch_ecb_rates()
        rates["EUR"] = 1.0  # base currency — ECB doesn't list it

        rep_session = ReportingSession()
        try:
            for currency, rate in rates.items():
                existing = (
                    rep_session.query(FxRate)
                    .filter_by(
                        as_of_date=ecb_date,
                        base_currency="EUR",
                        quote_currency=currency,
                    )
                    .first()
                )
                if existing:
                    existing.rate = rate
                else:
                    rep_session.add(
                        FxRate(
                            as_of_date=ecb_date,
                            base_currency="EUR",
                            quote_currency=currency,
                            rate=rate,
                        )
                    )
            rep_session.commit()
            log.info("Upserted %d FX rates for %s", len(rates), ecb_date)
        except Exception:
            rep_session.rollback()
            raise
        finally:
            rep_session.close()

        # ---- Step 2: Build position snapshot --------------------------------
        gf_session = GhostfolioSession()
        rep_session2 = ReportingSession()
        try:
            rows = build_position_snapshot(
                as_of_date=as_of_date,
                gf_session=gf_session,
                rep_session=rep_session2,
                user_id_filter=settings.ghostfolio_owner_id_or_none,
                account_id_filter=None,
            )
        finally:
            gf_session.close()
            rep_session2.close()

        # ---- Step 3: Write snapshot -----------------------------------------
        write_session = ReportingSession()
        try:
            write_session.query(PositionSnapshot).filter(
                PositionSnapshot.as_of_date == as_of_date,
            ).delete()

            for row in rows:
                write_session.add(PositionSnapshot(**row))

            write_session.commit()
            run_record.rows_written = len(rows)
            log.info(
                "position_snapshot %s: wrote %d rows", as_of_date, len(rows)
            )
        except Exception:
            write_session.rollback()
            raise
        finally:
            write_session.close()
