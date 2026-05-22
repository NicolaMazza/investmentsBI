"""ECB daily FX rate fetcher.

Fetches the ECB eurofxref-daily.xml feed and returns a mapping of
{ISO-4217 currency code → rate}, where rate = how many units of that
currency per 1 EUR (ECB convention, base = EUR).

EUR itself is not listed by the ECB; callers should add it as rate 1.0.
"""
from __future__ import annotations

import datetime
import logging
import xml.etree.ElementTree as ET

from app.fetchers.base import fetch_with_cache

log = logging.getLogger(__name__)

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_ECB_NS = "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"


def fetch_ecb_rates() -> tuple[datetime.date, dict[str, float]]:
    """Fetch ECB daily rates.

    Returns
    -------
    (as_of_date, rates)
        as_of_date : the publication date reported by ECB
        rates      : {currency_code: rate_per_eur}, e.g. {"USD": 1.1234, "GBP": 0.8567, ...}
    """
    raw = fetch_with_cache(ECB_URL)
    return _parse_ecb_xml(raw.decode("utf-8"))


def _parse_ecb_xml(xml_text: str) -> tuple[datetime.date, dict[str, float]]:
    root = ET.fromstring(xml_text)

    # Navigate: Envelope → Cube → Cube[time=...] → Cube[currency=...]
    outer = root.find(f"{{{_ECB_NS}}}Cube")
    if outer is None:
        raise ValueError("ECB XML: missing outer <Cube> element")

    daily = outer.find(f"{{{_ECB_NS}}}Cube")
    if daily is None:
        raise ValueError("ECB XML: missing daily <Cube time=...> element")

    date_str = daily.get("time")
    if not date_str:
        raise ValueError("ECB XML: daily Cube has no 'time' attribute")
    as_of_date = datetime.date.fromisoformat(date_str)

    rates: dict[str, float] = {}
    for cube in daily:
        currency = cube.get("currency")
        rate_str = cube.get("rate")
        if not currency or not rate_str:
            continue
        try:
            rates[currency.upper()] = float(rate_str)
        except (ValueError, TypeError):
            log.warning("ECB XML: could not parse rate for %s: %r", currency, rate_str)

    log.info("ECB FX: fetched %d rates for %s", len(rates), as_of_date)
    return as_of_date, rates
