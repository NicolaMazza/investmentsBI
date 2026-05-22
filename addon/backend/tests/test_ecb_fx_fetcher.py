from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import patch

from app.fetchers.ecb_fx import _parse_ecb_xml, fetch_ecb_rates

FIXTURE = Path(__file__).parent / "fixtures" / "ecb_daily.xml"


def test_parse_ecb_xml_date() -> None:
    xml_text = FIXTURE.read_text(encoding="utf-8")
    date, rates = _parse_ecb_xml(xml_text)
    assert date == datetime.date(2026, 5, 21)


def test_parse_ecb_xml_rates() -> None:
    xml_text = FIXTURE.read_text(encoding="utf-8")
    _, rates = _parse_ecb_xml(xml_text)
    assert abs(rates["USD"] - 1.1234) < 1e-6
    assert abs(rates["GBP"] - 0.8567) < 1e-6
    assert abs(rates["JPY"] - 164.23) < 1e-6


def test_parse_ecb_xml_all_currencies() -> None:
    xml_text = FIXTURE.read_text(encoding="utf-8")
    _, rates = _parse_ecb_xml(xml_text)
    assert "EUR" not in rates  # ECB doesn't list its own base; caller adds it
    assert len(rates) == 10


def test_fetch_ecb_rates_calls_cache() -> None:
    raw = FIXTURE.read_bytes()
    with patch("app.fetchers.ecb_fx.fetch_with_cache", return_value=raw) as mock_cache:
        date, rates = fetch_ecb_rates()
    mock_cache.assert_called_once()
    assert date == datetime.date(2026, 5, 21)
    assert "USD" in rates
