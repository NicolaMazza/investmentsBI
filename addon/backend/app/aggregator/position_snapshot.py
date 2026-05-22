"""Position snapshot aggregator.

Reads Ghostfolio Orders + MarketData (read-only), combines with FX rates
from our reporting DB, and returns a list of PositionSnapshotRow dicts
ready to insert into `position_snapshot`.

Design notes
------------
- Only BUY and SELL orders affect quantity; DIVIDEND/FEE etc. are ignored.
- Positions with quantity <= 0 after netting are excluded (fully divested).
- Market price = latest MarketData.marketPrice with date <= as_of_date.
- FX rate  = latest fx_rate row with as_of_date <= target date, base EUR.
- If no market price is found the row is still written (price/value = NULL).
- If no FX rate is found the EUR value is NULL but the row is still written.
"""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal
from typing import TypedDict

from sqlalchemy import cast, func, tuple_
from sqlalchemy import Text as SAText
from sqlalchemy.orm import Session

from app.db.ghostfolio import MarketData, Order, SymbolProfile
from app.db.reporting import FxRate

log = logging.getLogger(__name__)


class PositionSnapshotRow(TypedDict):
    as_of_date: datetime.date
    account_id: str
    symbol_profile_id: str
    isin: str | None
    symbol: str | None
    name: str | None
    currency: str | None
    quantity: float
    market_price_native: float | None
    market_value_native: float | None
    fx_rate_to_eur: float | None
    market_value_eur: float | None


# ---------------------------------------------------------------------------
# Query helpers — thin wrappers kept separate so tests can mock them cleanly
# ---------------------------------------------------------------------------

def _query_orders(
    gf_session: Session,
    cutoff: datetime.datetime,
    account_id_filter: str | None,
) -> list[tuple[Order, SymbolProfile]]:
    """Return all BUY/SELL orders up to cutoff, joined to SymbolProfile."""
    q = (
        gf_session.query(Order, SymbolProfile)
        .join(SymbolProfile, Order.symbolProfileId == SymbolProfile.id)
        .filter(Order.date < cutoff)
        .filter(cast(Order.type, SAText).in_(["BUY", "SELL"]))
    )
    if account_id_filter:
        q = q.filter(Order.accountId == account_id_filter)
    return q.all()


def _query_latest_prices(
    gf_session: Session,
    symbol_source_pairs: set[tuple[str, str]],
    cutoff: datetime.datetime,  # must be timezone-naive (MarketData.date is TIMESTAMP)
) -> dict[tuple[str, str], float]:
    """Return latest marketPrice per (symbol, dataSource) pair up to cutoff."""
    if not symbol_source_pairs:
        return {}

    max_date_subq = (
        gf_session.query(
            MarketData.symbol,
            MarketData.dataSource,
            func.max(MarketData.date).label("max_date"),
        )
        .filter(
            tuple_(MarketData.symbol, MarketData.dataSource).in_(symbol_source_pairs),
            MarketData.date < cutoff,
        )
        .group_by(MarketData.symbol, MarketData.dataSource)
        .subquery()
    )

    rows = (
        gf_session.query(MarketData)
        .join(
            max_date_subq,
            (MarketData.symbol == max_date_subq.c.symbol)
            & (MarketData.dataSource == max_date_subq.c.dataSource)
            & (MarketData.date == max_date_subq.c.max_date),
        )
        .all()
    )

    return {(r.symbol, r.dataSource): float(r.marketPrice) for r in rows}


def _query_fx_rates(
    rep_session: Session,
    currencies: set[str],
    as_of_date: datetime.date,
) -> dict[str, float]:
    """Return latest EUR FX rate per currency up to as_of_date."""
    result: dict[str, float] = {"EUR": 1.0}
    non_eur = currencies - {"EUR"}
    if not non_eur:
        return result

    max_fx_subq = (
        rep_session.query(
            FxRate.quote_currency,
            func.max(FxRate.as_of_date).label("max_date"),
        )
        .filter(
            FxRate.base_currency == "EUR",
            FxRate.quote_currency.in_(non_eur),
            FxRate.as_of_date <= as_of_date,
        )
        .group_by(FxRate.quote_currency)
        .subquery()
    )

    rows = (
        rep_session.query(FxRate)
        .join(
            max_fx_subq,
            (FxRate.quote_currency == max_fx_subq.c.quote_currency)
            & (FxRate.as_of_date == max_fx_subq.c.max_date)
            & (FxRate.base_currency == "EUR"),
        )
        .all()
    )

    for r in rows:
        result[r.quote_currency] = float(r.rate)
    return result


# ---------------------------------------------------------------------------
# Main aggregation
# ---------------------------------------------------------------------------

def build_position_snapshot(
    as_of_date: datetime.date,
    gf_session: Session,
    rep_session: Session,
    account_id_filter: str | None = None,
) -> list[PositionSnapshotRow]:
    """Compute net positions and return rows for position_snapshot.

    Parameters
    ----------
    as_of_date       : snapshot date (orders and prices up to this date)
    gf_session       : read-only session on the Ghostfolio database
    rep_session      : session on the investments_bi database (for FX rates)
    account_id_filter: if set, only include orders from this Ghostfolio account
    """
    # date column is "timestamp without time zone" — keep cutoff naive
    cutoff = datetime.datetime.combine(
        as_of_date + datetime.timedelta(days=1),
        datetime.time.min,
    )

    # ---- 1. Net quantities ------------------------------------------------
    order_rows = _query_orders(gf_session, cutoff, account_id_filter)

    meta: dict[tuple[str, str], dict] = {}   # (account_id, sp_id) -> info
    qty: dict[tuple[str, str], Decimal] = {}

    for order, sp in order_rows:
        acct = order.accountId or "unknown"
        key = (acct, sp.id)

        if key not in meta:
            meta[key] = {
                "symbol_profile_id": sp.id,
                "isin": sp.isin,
                "symbol": sp.symbol,
                "name": sp.name,
                "currency": sp.currency,
                "data_source": sp.dataSource,
            }
            qty[key] = Decimal("0")

        if order.type == "BUY":
            qty[key] += order.quantity
        else:
            qty[key] -= order.quantity

    active = {k: v for k, v in qty.items() if v > 0}
    if not active:
        log.info("position_snapshot %s: no active positions found", as_of_date)
        return []

    log.info("position_snapshot %s: %d active positions", as_of_date, len(active))

    # ---- 2. Latest market prices ------------------------------------------
    symbol_source_pairs = {
        (meta[k]["symbol"], meta[k]["data_source"]) for k in active
    }
    prices = _query_latest_prices(gf_session, symbol_source_pairs, cutoff)

    missing_prices = symbol_source_pairs - prices.keys()
    if missing_prices:
        log.warning(
            "position_snapshot %s: no market price for %d symbol(s): %s",
            as_of_date,
            len(missing_prices),
            ", ".join(f"{s}/{d}" for s, d in sorted(missing_prices)),
        )

    # ---- 3. FX rates -------------------------------------------------------
    currencies = {meta[k]["currency"] for k in active}
    fx_rates = _query_fx_rates(rep_session, currencies, as_of_date)

    missing_fx = {c for c in currencies if c not in fx_rates}
    if missing_fx:
        log.warning(
            "position_snapshot %s: no FX rate for %s — EUR values will be NULL",
            as_of_date,
            ", ".join(sorted(missing_fx)),
        )

    # ---- 4. Assemble rows --------------------------------------------------
    rows: list[PositionSnapshotRow] = []
    for (acct, _sp_id), quantity in active.items():
        m = meta[(acct, _sp_id)]
        currency = m["currency"]
        quantity_f = float(quantity)

        market_price = prices.get((m["symbol"], m["data_source"]))
        market_value_native = (
            round(quantity_f * market_price, 2) if market_price is not None else None
        )

        rate = fx_rates.get(currency)
        market_value_eur = (
            round(market_value_native / rate, 2)
            if (market_value_native is not None and rate is not None)
            else None
        )

        rows.append(
            PositionSnapshotRow(
                as_of_date=as_of_date,
                account_id=acct,
                symbol_profile_id=m["symbol_profile_id"],
                isin=m["isin"],
                symbol=m["symbol"],
                name=m["name"],
                currency=currency,
                quantity=quantity_f,
                market_price_native=market_price,
                market_value_native=market_value_native,
                fx_rate_to_eur=rate,
                market_value_eur=market_value_eur,
            )
        )

    return rows
