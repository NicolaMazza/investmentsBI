"""Vanguard XLSX holdings fetcher.

Finding the download URL
------------------------
Vanguard does not publish a stable direct URL.  You need to capture it once
from your browser's DevTools:
  1. Open the Vanguard product page, e.g.:
       https://www.vanguard.co.uk/professional/product/etf/equity/9681/...
  2. Open DevTools → Network tab, clear the log.
  3. Scroll to "Holdings details" and click the Download button.
  4. In the Network tab, find the request for an .xlsx file.
  5. Copy the URL and run:
       UPDATE product SET source_url = '<url>' WHERE isin = 'IE00BK5BQX27';

Column mapping (VWCG, Vanguard FTSE Developed Europe UCITS ETF):
    "Holding name"               → constituent_name
    "ISIN"                       → constituent_isin  (preferred)
    "SEDOL"                      → constituent_isin  (fallback, with warning)
    "% of fund net assets"       → weight_pct  (already in %)
    "Country of domicile"        → country_listing
    "Currency"                   → native_currency
    "Market value (...)"         → market_value_native
    "Quantity" / "Number of…"    → shares

Header detection
----------------
The spreadsheet has ~9 metadata rows before the actual data.  We locate the
header by scanning for the row that contains a cell with "holding name"
(case-insensitive).

Sector note
-----------
Vanguard UCITS XLSX files do not include a sector column.  Constituents of
VWCG will show as 'Unknown' for the sector dimension.  If sector enrichment is
needed, it can be added via the instrument_reference table in M7.
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

# Known column name variants across different Vanguard UCITS fund downloads.
_WEIGHT_COLS   = ["% of fund net assets", "Percentage of fund", "Weighting", "Weighting (%)",
                  "% of Fund", "% Net Assets"]
_NAME_COLS     = ["Holding name", "Name", "Security name", "Stock name"]
_COUNTRY_COLS  = ["Country of domicile", "Country of risk", "Country", "Domicile"]
_CURRENCY_COLS = ["Currency", "Local currency", "Dealing currency", "Ccy"]
_SHARES_COLS   = ["Quantity", "Number of shares", "Shares", "Nominal"]
_MV_COLS       = ["Market value (local currency)", "Market value (EUR)",
                  "Market value (GBP)", "Market value", "Market Value"]


def _pick(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Return the first candidate that is a column in *df*, else None."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


@register("vanguard_xlsx")
class VanguardFetcher(BaseFetcher):
    def fetch(self, product: "Product") -> list[NormalizedHolding]:
        if not product.source_url:
            raise ValueError(
                f"Product {product.isin} has no source_url.  "
                "See the module docstring for instructions on capturing the "
                "Vanguard download URL via browser DevTools."
            )

        raw = fetch_with_cache(product.source_url)
        buf = io.BytesIO(raw)

        # ── Step 1: find the header row ───────────────────────────────────────
        probe = pd.read_excel(buf, header=None, engine="openpyxl")
        header_row: int | None = None
        for idx, row in probe.iterrows():
            for cell in row.values:
                if isinstance(cell, str) and "holding name" in cell.lower():
                    header_row = int(str(idx))
                    break
            if header_row is not None:
                break

        if header_row is None:
            raise ValueError(
                f"Vanguard XLSX for {product.isin}: could not find header row. "
                "Expected a cell containing 'Holding name'.  "
                f"Columns in first row: {probe.iloc[0].tolist()}"
            )

        # ── Step 2: re-read with the correct header ───────────────────────────
        buf.seek(0)
        df = pd.read_excel(buf, header=header_row, engine="openpyxl")
        df.columns = df.columns.str.strip()
        df = df.dropna(how="all")  # strip trailing blank rows Vanguard appends
        log.debug("Vanguard %s columns: %s", product.isin, df.columns.tolist())

        # ── Step 3: resolve column names ──────────────────────────────────────
        weight_col   = _pick(df, _WEIGHT_COLS)
        name_col     = _pick(df, _NAME_COLS)
        country_col  = _pick(df, _COUNTRY_COLS)
        currency_col = _pick(df, _CURRENCY_COLS)
        shares_col   = _pick(df, _SHARES_COLS)
        mv_col       = _pick(df, _MV_COLS)

        if weight_col is None:
            raise ValueError(
                f"Vanguard XLSX for {product.isin}: no weight column found. "
                f"Columns present: {df.columns.tolist()}"
            )

        # Prefer ISIN; fall back to SEDOL (UK 7-char code).
        if "ISIN" in df.columns:
            id_col = "ISIN"
        elif "SEDOL" in df.columns:
            id_col = "SEDOL"
            log.warning(
                "Vanguard %s: no ISIN column found — using SEDOL as constituent "
                "identifier.  Cross-product look-through joins will be unreliable "
                "until an ISIN source is added.",
                product.isin,
            )
        else:
            raise ValueError(
                f"Vanguard XLSX for {product.isin}: no ISIN or SEDOL column. "
                f"Columns present: {df.columns.tolist()}"
            )

        # ── Step 4: parse rows ────────────────────────────────────────────────
        df = df.dropna(subset=[id_col])
        df = df[df[id_col].astype(str).str.strip().isin(["", "nan"]) == False]  # noqa: E712

        def _safe_float(val: object) -> float | None:
            if val is None:
                return None
            try:
                result = float(str(val).replace(",", "").replace("%", "").strip())
                return result if result else None
            except (ValueError, TypeError):
                return None

        holdings: list[NormalizedHolding] = []
        for _, row in df.iterrows():
            isin = str(row[id_col]).strip()
            if not isin or isin.lower() in ("nan", "none", ""):
                continue

            weight = _safe_float(row[weight_col]) or 0.0
            name   = str(row[name_col]).strip() if name_col else None
            if name in ("nan", "None", ""):
                name = None

            holdings.append(
                NormalizedHolding(
                    constituent_isin=isin,
                    constituent_name=name,
                    weight_pct=weight,
                    country_listing=normalize_country(
                        str(row[country_col]).strip() if country_col else None
                    ),
                    native_currency=normalize_currency(
                        str(row[currency_col]).strip() if currency_col else None
                    ),
                    market_value_native=_safe_float(row[mv_col]) if mv_col else None,
                    shares=_safe_float(row[shares_col]) if shares_col else None,
                )
            )

        log.info("Vanguard %s: fetched %d holdings", product.isin, len(holdings))
        return holdings
