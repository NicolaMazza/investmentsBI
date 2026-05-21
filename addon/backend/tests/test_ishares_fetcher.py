from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.fetchers.ishares import ISharesFetcher

FIXTURE = Path(__file__).parent / "fixtures" / "iwda_sample.csv"


def _make_product(isin: str = "IE00B4L5Y983", url: str = "https://example.com/holdings.csv") -> MagicMock:
    p = MagicMock()
    p.isin = isin
    p.source_url = url
    return p


def test_parses_sample_csv() -> None:
    raw = FIXTURE.read_bytes()
    with patch("app.fetchers.ishares.fetch_with_cache", return_value=raw):
        holdings = ISharesFetcher().fetch(_make_product())

    assert len(holdings) == 5
    tickers = {h.ticker for h in holdings}
    assert "AAPL" in tickers
    assert "MSFT" in tickers


def test_weight_parsed_correctly() -> None:
    raw = FIXTURE.read_bytes()
    with patch("app.fetchers.ishares.fetch_with_cache", return_value=raw):
        holdings = ISharesFetcher().fetch(_make_product())

    aapl = next(h for h in holdings if h.ticker == "AAPL")
    assert abs(aapl.weight_pct - 4.95) < 0.001
    assert aapl.sector == "Information Technology"
    assert aapl.country_listing == "United States"
    assert aapl.native_currency == "USD"


def test_ticker_used_as_identifier_when_no_isin() -> None:
    # UK iShares CSVs have no ISIN column — Ticker is used as constituent_isin
    raw = FIXTURE.read_bytes()
    with patch("app.fetchers.ishares.fetch_with_cache", return_value=raw):
        holdings = ISharesFetcher().fetch(_make_product())

    assert all(h.constituent_isin == h.ticker for h in holdings)


def test_source_url_required() -> None:
    product = _make_product()
    product.source_url = None
    with pytest.raises(ValueError, match="no source_url"):
        ISharesFetcher().fetch(product)
