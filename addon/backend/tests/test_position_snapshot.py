"""Unit tests for the position snapshot aggregator.

The three private query helpers (_query_orders, _query_latest_prices,
_query_fx_rates) are mocked so no real database connection is needed.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.aggregator.position_snapshot import build_position_snapshot
from app.db.ghostfolio import MarketData, Order, SymbolProfile
from app.db.reporting import FxRate

# ---------------------------------------------------------------------------
# Tiny helpers to build plain ORM-like objects without a real session
# ---------------------------------------------------------------------------

def _sp(id: str, symbol: str, currency: str, isin: str | None = None, name: str | None = None) -> SymbolProfile:
    sp = SymbolProfile()
    sp.id = id
    sp.symbol = symbol
    sp.currency = currency
    sp.dataSource = "YAHOO"
    sp.isin = isin
    sp.name = name
    return sp


def _order(id: str, account_id: str, sp_id: str, type_: str, quantity: str, date: datetime.datetime) -> Order:
    o = Order()
    o.id = id
    o.accountId = account_id
    o.symbolProfileId = sp_id
    o.type = type_
    o.quantity = Decimal(quantity)
    o.unitPrice = Decimal("100")
    o.fee = Decimal("0")
    o.date = date
    o.userId = "user1"
    return o


def _fx_rate(quote: str, rate: float, date: datetime.date) -> FxRate:
    fx = FxRate()
    fx.base_currency = "EUR"
    fx.quote_currency = quote
    fx.rate = rate
    fx.as_of_date = date
    return fx


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

AS_OF = datetime.date(2026, 5, 21)
ACCOUNT = "f87e1c57-0c23-46d5-b553-014f643916b4"
T_PAST = datetime.datetime(2025, 1, 15, 12, 0, tzinfo=datetime.timezone.utc)
T_RECENT = datetime.datetime(2026, 5, 20, 16, 0, tzinfo=datetime.timezone.utc)

SP_AAPL = _sp("sp-aapl", "AAPL", "USD", isin="US0378331005", name="Apple Inc")
SP_NVDA = _sp("sp-nvda", "NVDA", "USD", isin="US67066G1040", name="NVIDIA Corp")

ORDER_BUY_AAPL   = _order("o1", ACCOUNT, "sp-aapl", "BUY",  "10", T_PAST)
ORDER_BUY_AAPL_2 = _order("o2", ACCOUNT, "sp-aapl", "BUY",  "5",  T_PAST)
ORDER_SELL_AAPL  = _order("o3", ACCOUNT, "sp-aapl", "SELL", "3",  T_RECENT)
ORDER_BUY_NVDA   = _order("o4", ACCOUNT, "sp-nvda", "BUY",  "8",  T_PAST)

PRICES_AAPL = {("AAPL", "YAHOO"): 200.0}
PRICES_NVDA = {("NVDA", "YAHOO"): 900.0}
PRICES_BOTH = {**PRICES_AAPL, **PRICES_NVDA}
FX_USD = {"EUR": 1.0, "USD": 1.12}

# Dummy sessions — the query helpers are patched, so sessions are never called
GF_SESSION = MagicMock()
REP_SESSION = MagicMock()

_PATCH_ORDERS  = "app.aggregator.position_snapshot._query_orders"
_PATCH_PRICES  = "app.aggregator.position_snapshot._query_latest_prices"
_PATCH_FX      = "app.aggregator.position_snapshot._query_fx_rates"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_net_quantity() -> None:
    """BUY 10 + BUY 5 - SELL 3 = 12 for AAPL."""
    order_rows = [
        (ORDER_BUY_AAPL, SP_AAPL),
        (ORDER_BUY_AAPL_2, SP_AAPL),
        (ORDER_SELL_AAPL, SP_AAPL),
    ]
    with patch(_PATCH_ORDERS, return_value=order_rows), \
         patch(_PATCH_PRICES, return_value=PRICES_AAPL), \
         patch(_PATCH_FX, return_value=FX_USD):
        rows = build_position_snapshot(AS_OF, GF_SESSION, REP_SESSION, ACCOUNT)

    assert len(rows) == 1
    assert abs(rows[0]["quantity"] - 12.0) < 1e-6


def test_market_value_calculation() -> None:
    """market_value_native = quantity x price."""
    with patch(_PATCH_ORDERS, return_value=[(ORDER_BUY_AAPL, SP_AAPL)]), \
         patch(_PATCH_PRICES, return_value=PRICES_AAPL), \
         patch(_PATCH_FX, return_value=FX_USD):
        rows = build_position_snapshot(AS_OF, GF_SESSION, REP_SESSION, ACCOUNT)

    row = rows[0]
    assert abs(row["quantity"] - 10.0) < 1e-6
    assert row["market_price_native"] is not None
    assert abs(row["market_price_native"] - 200.0) < 1e-4
    assert abs(row["market_value_native"] - 2000.0) < 0.01


def test_eur_conversion() -> None:
    """market_value_eur = market_value_native / fx_rate_to_eur."""
    with patch(_PATCH_ORDERS, return_value=[(ORDER_BUY_AAPL, SP_AAPL)]), \
         patch(_PATCH_PRICES, return_value=PRICES_AAPL), \
         patch(_PATCH_FX, return_value=FX_USD):  # 1 EUR = 1.12 USD
        rows = build_position_snapshot(AS_OF, GF_SESSION, REP_SESSION, ACCOUNT)

    row = rows[0]
    assert abs(row["fx_rate_to_eur"] - 1.12) < 1e-6
    expected_eur = 2000.0 / 1.12
    assert abs(row["market_value_eur"] - expected_eur) < 0.01


def test_multiple_symbols() -> None:
    """Two symbols produce two rows."""
    order_rows = [
        (ORDER_BUY_AAPL, SP_AAPL),
        (ORDER_BUY_NVDA, SP_NVDA),
    ]
    with patch(_PATCH_ORDERS, return_value=order_rows), \
         patch(_PATCH_PRICES, return_value=PRICES_BOTH), \
         patch(_PATCH_FX, return_value=FX_USD):
        rows = build_position_snapshot(AS_OF, GF_SESSION, REP_SESSION, ACCOUNT)

    assert len(rows) == 2
    symbols = {r["symbol"] for r in rows}
    assert "AAPL" in symbols
    assert "NVDA" in symbols


def test_no_orders_returns_empty() -> None:
    with patch(_PATCH_ORDERS, return_value=[]), \
         patch(_PATCH_PRICES, return_value={}), \
         patch(_PATCH_FX, return_value={"EUR": 1.0}):
        rows = build_position_snapshot(AS_OF, GF_SESSION, REP_SESSION, ACCOUNT)

    assert rows == []


def test_null_market_value_when_no_price() -> None:
    """If MarketData has no row, market_value_* should be None."""
    with patch(_PATCH_ORDERS, return_value=[(ORDER_BUY_AAPL, SP_AAPL)]), \
         patch(_PATCH_PRICES, return_value={}), \
         patch(_PATCH_FX, return_value=FX_USD):
        rows = build_position_snapshot(AS_OF, GF_SESSION, REP_SESSION, ACCOUNT)

    row = rows[0]
    assert row["market_price_native"] is None
    assert row["market_value_native"] is None
    assert row["market_value_eur"] is None


def test_null_eur_value_when_no_fx() -> None:
    """If no FX rate, market_value_eur should be None."""
    with patch(_PATCH_ORDERS, return_value=[(ORDER_BUY_AAPL, SP_AAPL)]), \
         patch(_PATCH_PRICES, return_value=PRICES_AAPL), \
         patch(_PATCH_FX, return_value={"EUR": 1.0}):  # USD not in fx_rates
        rows = build_position_snapshot(AS_OF, GF_SESSION, REP_SESSION, ACCOUNT)

    row = rows[0]
    assert row["market_price_native"] is not None
    assert row["market_value_eur"] is None


def test_isin_and_name_propagated() -> None:
    with patch(_PATCH_ORDERS, return_value=[(ORDER_BUY_AAPL, SP_AAPL)]), \
         patch(_PATCH_PRICES, return_value=PRICES_AAPL), \
         patch(_PATCH_FX, return_value=FX_USD):
        rows = build_position_snapshot(AS_OF, GF_SESSION, REP_SESSION, ACCOUNT)

    row = rows[0]
    assert row["isin"] == "US0378331005"
    assert row["name"] == "Apple Inc"


def test_fully_sold_position_excluded() -> None:
    """BUY 5 then SELL 5 = zero position, should not appear in output."""
    sell_all = _order("o-sell", ACCOUNT, "sp-aapl", "SELL", "10", T_RECENT)
    order_rows = [(ORDER_BUY_AAPL, SP_AAPL), (sell_all, SP_AAPL)]
    with patch(_PATCH_ORDERS, return_value=order_rows), \
         patch(_PATCH_PRICES, return_value=PRICES_AAPL), \
         patch(_PATCH_FX, return_value=FX_USD):
        rows = build_position_snapshot(AS_OF, GF_SESSION, REP_SESSION, ACCOUNT)

    assert rows == []
