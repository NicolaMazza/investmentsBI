"""HSBC XLSX holdings fetcher.

Download URL (stable as of 2026):
    https://www.assetmanagement.hsbc.co.uk/api/v1/download/document/{isin}/gb/en/holdings

Column mapping (HSBC MSCI Emerging Markets UCITS ETF, IE000KCS7J59):
    SecurityName         → constituent_name
    ISIN                 → constituent_isin
    Country              → country_listing
    LocalCurrencyCode    → native_currency   (already ISO-4217, e.g. "TWD")
    NumberOfShares       → shares
    MarketValue          → market_value_native
    Weighting            → weight_pct  (see note below)

Weight normalisation
--------------------
HSBC stores Weighting as a decimal fraction (0.085 = 8.5 %).  The schema
stores weight_pct as a percentage (8.5).  We detect the format automatically:
if the raw sum is < 5 the values are decimal → multiply by 100.

Note: Sector data is not included in the HSBC holdings download.  The sector
dimension for H4Z3 constituents will fall back to 'Unknown' until a
supplementary sector source is added (out of scope for M6).
"""
from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

import pandas as pd

from app.fetchers.base import (
    BaseFetcher,
    NormalizedHolding,
    fetch_with_cache,
    normalize_country,
    normalize_currency,
    register,
)

if TYPE_CHECKING:
    from app.db.reporting import Product

log = logging.getLogger(__name__)


def _read_excel(raw: bytes) -> pd.DataFrame:
    """Read an Excel file, handling both .xlsx (ZIP) and .xls (binary OLE2) formats.

    HSBC serves their holdings file as old-format .xls despite the URL suggesting
    XLSX.  We detect the format from the magic bytes rather than trusting the
    Content-Type or file extension.
    """
    buf = io.BytesIO(raw)
    # XLSX files are ZIP archives; magic bytes are PK\\x03\\x04
    if raw[:4] == b"PK\x03\x04":
        return pd.read_excel(buf, engine="openpyxl")
    # Old binary XLS (OLE2 compound document); magic bytes are D0 CF 11 E0
    try:
        buf.seek(0)
        return pd.read_excel(buf, engine="xlrd")
    except Exception as xlrd_err:
        log.debug("xlrd failed (%s), trying HTML fallback", xlrd_err)
    # Last resort: some issuers serve an HTML table with an .xls extension
    buf.seek(0)
    tables = pd.read_html(buf)
    if tables:
        return tables[0]
    raise ValueError("Could not parse HSBC file as XLSX, XLS, or HTML")


@register("hsbc_xlsx")
class HsbcFetcher(BaseFetcher):
    def fetch(self, product: "Product") -> list[NormalizedHolding]:
        if not product.source_url:
            raise ValueError(
                f"Product {product.isin} has no source_url — "
                "expected https://www.assetmanagement.hsbc.co.uk/api/v1/download/"
                "document/{isin}/gb/en/holdings"
            )

        raw = fetch_with_cache(product.source_url)
        df = _read_excel(raw)
        df.columns = df.columns.str.strip()
        log.debug("HSBC %s columns: %s", product.isin, df.columns.tolist())

        # Require the minimum columns we need.
        required = {"ISIN", "Weighting"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(
                f"HSBC XLSX for {product.isin}: missing expected columns {missing}. "
                f"Columns present: {df.columns.tolist()}"
            )

        # Drop rows with no ISIN.
        df = df.dropna(subset=["ISIN"])
        df = df[df["ISIN"].astype(str).str.strip().str.len() > 0]
        df = df[df["ISIN"].astype(str).str.strip() != "nan"]

        # Detect weight format: HSBC uses decimal (0–1) or percent (0–100).
        raw_weights = pd.to_numeric(df["Weighting"], errors="coerce").dropna()
        weight_scale = 100.0 if raw_weights.sum() < 5.0 else 1.0
        if weight_scale == 100.0:
            log.debug("HSBC %s: Weighting column is decimal → multiplying by 100", product.isin)

        holdings: list[NormalizedHolding] = []
        for _, row in df.iterrows():
            isin = str(row["ISIN"]).strip()
            if not isin or isin.lower() == "nan":
                continue

            try:
                weight = float(str(row["Weighting"]).replace(",", "")) * weight_scale
            except (ValueError, TypeError):
                weight = 0.0

            def _float(col: str) -> float | None:
                val = row.get(col)
                if val is None:
                    return None
                try:
                    result = float(str(val).replace(",", "").strip())
                    return result if result else None
                except (ValueError, TypeError):
                    return None

            holdings.append(
                NormalizedHolding(
                    constituent_isin=isin,
                    constituent_name=str(row.get("SecurityName", "")).strip() or None,
                    weight_pct=weight,
                    country_listing=normalize_country(str(row.get("Country", "")) or None),
                    native_currency=normalize_currency(str(row.get("LocalCurrencyCode", "")) or None),
                    market_value_native=_float("MarketValue"),
                    shares=_float("NumberOfShares"),
                )
            )

        log.info("HSBC %s: fetched %d holdings", product.isin, len(holdings))
        return holdings
