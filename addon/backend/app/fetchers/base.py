from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

if TYPE_CHECKING:
    from app.db.reporting import Product

log = logging.getLogger(__name__)

CACHE_DIR = Path("/data/cache")
USER_AGENT = "Mozilla/5.0 (InvestmentsBI/0.1)"
HTTP_TIMEOUT = 30.0
HTTP_RETRIES = 3

_CURRENCY_MAP: dict[str, str] = {
    "US DOLLAR": "USD", "EURO": "EUR", "BRITISH POUND": "GBP",
    "JAPANESE YEN": "JPY", "SWISS FRANC": "CHF", "CANADIAN DOLLAR": "CAD",
    "AUSTRALIAN DOLLAR": "AUD", "HONG KONG DOLLAR": "HKD",
    "SWEDISH KRONA": "SEK", "NORWEGIAN KRONE": "NOK", "DANISH KRONE": "DKK",
    "SINGAPORE DOLLAR": "SGD", "KOREAN WON": "KRW", "TAIWAN DOLLAR": "TWD",
    "NEW TAIWAN DOLLAR": "TWD", "INDIAN RUPEE": "INR",
}

_SECTOR_MAP: dict[str, str] = {
    "information technology": "Information Technology",
    "financials": "Financials",
    "health care": "Health Care",
    "healthcare": "Health Care",
    "consumer discretionary": "Consumer Discretionary",
    "communication services": "Communication Services",
    "telecommunications": "Communication Services",
    "industrials": "Industrials",
    "consumer staples": "Consumer Staples",
    "energy": "Energy",
    "utilities": "Utilities",
    "real estate": "Real Estate",
    "materials": "Materials",
    "cash and/or derivatives": "Cash & Derivatives",
    "cash": "Cash & Derivatives",
}


class NormalizedHolding(BaseModel):
    constituent_isin: str
    constituent_name: str | None = None
    ticker: str | None = None
    weight_pct: float
    sector: str | None = None
    country_listing: str | None = None
    country_incorp: str | None = None
    native_currency: str | None = None
    asset_class: str | None = None
    market_value_native: float | None = None
    shares: float | None = None


def normalize_currency(raw: str | None) -> str | None:
    if not raw:
        return None
    upper = raw.strip().upper()
    return _CURRENCY_MAP.get(upper, upper if len(upper) <= 4 else None)


def normalize_country(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw.strip().title() or None


def normalize_sector(raw: str | None) -> str | None:
    if not raw:
        return None
    return _SECTOR_MAP.get(raw.strip().lower(), raw.strip().title()) or None


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    return CACHE_DIR / digest


def fetch_with_cache(url: str) -> bytes:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    transport = httpx.HTTPTransport(retries=HTTP_RETRIES)
    with httpx.Client(transport=transport, timeout=HTTP_TIMEOUT) as client:
        resp = client.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        resp.raise_for_status()
        content = resp.content

    content_hash = hashlib.sha256(content).hexdigest()
    cache_file = _cache_path(url)
    hash_file = cache_file.with_suffix(".hash")

    if cache_file.exists() and hash_file.exists():
        if hash_file.read_text().strip() == content_hash:
            log.debug("Cache hit for %s", url)
            return cache_file.read_bytes()

    cache_file.write_bytes(content)
    hash_file.write_text(content_hash)
    log.debug("Cached response for %s", url)
    return content


class BaseFetcher(ABC):
    @abstractmethod
    def fetch(self, product: "Product") -> list[NormalizedHolding]:
        ...


# Parser registry — maps product.parser string → fetcher class
_registry: dict[str, type[BaseFetcher]] = {}


def register(parser_key: str) -> "type[type[BaseFetcher]]":
    def decorator(cls: type[BaseFetcher]) -> type[BaseFetcher]:
        _registry[parser_key] = cls
        return cls
    return decorator  # type: ignore[return-value]


def get_fetcher(parser_key: str) -> BaseFetcher:
    cls = _registry.get(parser_key)
    if cls is None:
        raise KeyError(f"No fetcher registered for parser key '{parser_key}'")
    return cls()


def load_all_fetchers() -> None:
    from app.fetchers import ishares  # noqa: F401
