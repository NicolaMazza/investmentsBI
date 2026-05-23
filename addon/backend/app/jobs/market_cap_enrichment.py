"""Job: enrich instrument_reference with market-cap data via yfinance.

Runs weekly (Sunday 01:00 by default).  Fetches market cap for every
unique constituent identifier in product_composition_snapshot, converts
to EUR using the latest FX rate, buckets into Large / Mid / Small / Micro,
and upserts into instrument_reference.

Market-cap buckets (EUR)
------------------------
  Large  : >= 10 B
  Mid    : >= 2 B  and < 10 B
  Small  : >= 300 M and < 2 B
  Micro  : < 300 M

yfinance notes
--------------
- Works best with exchange tickers (ASML, AAPL, AZN.L).
- Vanguard constituent_isin values ARE tickers, so they resolve well.
- iShares/HSBC constituent_isin values are ISINs; yfinance may or may
  not resolve them depending on Yahoo Finance's ISIN search support.
  Unresolvable symbols get NULL market_cap → 'Unknown' bucket.
- We use fast_info['market_cap'] (no full info download) for speed.

Manual trigger: POST /api/admin/refresh?job=market_cap_enrichment
"""
from __future__ import annotations

import datetime
import logging
import time

from sqlalchemy import select

from app.db.reporting import FxRate, InstrumentReference, ProductCompositionSnapshot
from app.db.reporting_session import SessionLocal
from app.jobs.base import job_context

log = logging.getLogger(__name__)

JOB_NAME = "market_cap_enrichment"

_LARGE_EUR  = 10_000_000_000
_MID_EUR    =  2_000_000_000
_SMALL_EUR  =    300_000_000

_BATCH_SIZE = 50   # yfinance calls per batch before a short pause
_PAUSE_SEC  = 2.0  # seconds between batches (rate-limit courtesy)


def _bucket(market_cap_eur: float | None) -> str | None:
    if market_cap_eur is None:
        return None
    if market_cap_eur >= _LARGE_EUR:
        return "Large Cap"
    if market_cap_eur >= _MID_EUR:
        return "Mid Cap"
    if market_cap_eur >= _SMALL_EUR:
        return "Small Cap"
    return "Micro Cap"


def _fx_to_eur(session: "Session", currency: str) -> float:  # type: ignore[name-defined]
    """Return EUR/currency rate (how many currency units per 1 EUR), or 1.0."""
    if not currency or currency.upper() == "EUR":
        return 1.0
    row = session.execute(
        select(FxRate).where(
            FxRate.quote_currency == currency.upper(),
            FxRate.base_currency  == "EUR",
        ).order_by(FxRate.as_of_date.desc()).limit(1)
    ).scalar()
    return float(row.rate) if row else 1.0


def run() -> None:
    try:
        import yfinance as yf
    except ImportError:
        log.error("market_cap_enrichment: yfinance not installed — skipping")
        return

    session = SessionLocal()
    try:
        # All unique constituent_isin values across all active snapshots
        symbols: list[str] = [
            row[0] for row in session.execute(
                select(ProductCompositionSnapshot.constituent_isin).distinct()
            ).all()
        ]
    finally:
        session.close()

    if not symbols:
        log.warning("market_cap_enrichment: no constituent symbols found")
        return

    log.info("market_cap_enrichment: enriching %d symbols", len(symbols))
    updated = skipped = 0

    with job_context(JOB_NAME) as run_record:
        fx_session = SessionLocal()
        write_session = SessionLocal()
        try:
            for i in range(0, len(symbols), _BATCH_SIZE):
                batch = symbols[i : i + _BATCH_SIZE]

                for symbol in batch:
                    try:
                        ticker = yf.Ticker(symbol)
                        fi = ticker.fast_info
                        mc_native: float | None = getattr(fi, "market_cap", None)
                        currency: str = getattr(fi, "currency", "") or ""

                        if mc_native and mc_native > 0:
                            rate   = _fx_to_eur(fx_session, currency)
                            mc_eur = mc_native / rate if rate else None
                        else:
                            mc_eur = None

                        existing = write_session.get(InstrumentReference, symbol)
                        if existing:
                            existing.market_cap_eur    = mc_eur
                            existing.market_cap_bucket = _bucket(mc_eur)
                            existing.last_refreshed_at = datetime.datetime.utcnow()
                        else:
                            write_session.add(InstrumentReference(
                                isin               = symbol,
                                market_cap_eur     = mc_eur,
                                market_cap_bucket  = _bucket(mc_eur),
                                last_refreshed_at  = datetime.datetime.utcnow(),
                            ))

                        if mc_eur:
                            updated += 1
                        else:
                            skipped += 1

                    except Exception as exc:
                        log.debug("market_cap_enrichment: %s → %s", symbol, exc)
                        skipped += 1

                write_session.commit()
                if i + _BATCH_SIZE < len(symbols):
                    time.sleep(_PAUSE_SEC)

            log.info(
                "market_cap_enrichment: done — %d enriched, %d skipped/unknown",
                updated, skipped,
            )
            run_record.rows_written = updated

        except Exception:
            write_session.rollback()
            log.exception("market_cap_enrichment: write failed")
            raise
        finally:
            fx_session.close()
            write_session.close()
