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
    normalize_sector,
    register,
)

if TYPE_CHECKING:
    from app.db.reporting import Product

log = logging.getLogger(__name__)


@register("ishares_csv")
class ISharesFetcher(BaseFetcher):
    def fetch(self, product: "Product") -> list[NormalizedHolding]:
        if not product.source_url:
            raise ValueError(f"Product {product.isin} has no source_url")

        raw = fetch_with_cache(product.source_url)
        text = raw.decode("utf-8-sig")

        # Skip metadata rows — find the header line starting with "Ticker"
        lines = text.splitlines()
        header_idx = next(
            (i for i, line in enumerate(lines) if line.startswith("Ticker")),
            None,
        )
        if header_idx is None:
            raise ValueError(f"Could not find header row in iShares CSV for {product.isin}")

        csv_body = "\n".join(lines[header_idx:])
        df = pd.read_csv(io.StringIO(csv_body), thousands=",")
        df.columns = df.columns.str.strip()
        log.debug("iShares %s columns: %s", product.isin, df.columns.tolist())

        # UK iShares CSVs do not include an ISIN column — fall back to Ticker
        if "ISIN" in df.columns:
            id_col = "ISIN"
        elif "Ticker" in df.columns:
            id_col = "Ticker"
            log.warning("iShares %s: no ISIN column, using Ticker as constituent identifier", product.isin)
        else:
            raise ValueError(f"iShares CSV for {product.isin} has neither ISIN nor Ticker column")

        df = df.dropna(subset=[id_col])
        df = df[df[id_col].str.strip() != ""]

        holdings: list[NormalizedHolding] = []
        for _, row in df.iterrows():
            isin = str(row.get(id_col, "")).strip()
            if not isin:
                continue

            try:
                weight = float(str(row.get("Weight (%)", 0)).replace(",", ""))
            except (ValueError, TypeError):
                weight = 0.0

            try:
                market_value = float(str(row.get("Market Value", "")).replace(",", "")) or None
            except (ValueError, TypeError):
                market_value = None

            try:
                shares = float(str(row.get("Shares", "")).replace(",", "")) or None
            except (ValueError, TypeError):
                shares = None

            holdings.append(
                NormalizedHolding(
                    constituent_isin=isin,
                    constituent_name=str(row.get("Name", "")).strip() or None,
                    ticker=str(row.get("Ticker", "")).strip() or None,
                    weight_pct=weight,
                    sector=normalize_sector(str(row.get("Sector", ""))),
                    country_listing=normalize_country(str(row.get("Location", ""))),
                    native_currency=normalize_currency(str(row.get("Market Currency", ""))),
                    asset_class=str(row.get("Asset Class", "")).strip() or None,
                    market_value_native=market_value,
                    shares=shares,
                )
            )

        # Deduplicate by constituent_isin — same ticker can appear on multiple
        # exchanges (e.g. SAN on NYSE and Madrid). Sum the weights but keep
        # metadata (country_listing, sector, currency etc.) from the
        # highest-weight occurrence, which is the primary listing.
        deduped: dict[str, NormalizedHolding] = {}
        weight_totals: dict[str, float] = {}
        for h in holdings:
            key = h.constituent_isin
            weight_totals[key] = weight_totals.get(key, 0.0) + h.weight_pct
            if key not in deduped or h.weight_pct > deduped[key].weight_pct:
                deduped[key] = h
        for key, total in weight_totals.items():
            deduped[key] = deduped[key].model_copy(update={"weight_pct": total})
        holdings = list(deduped.values())

        if len(holdings) < len(weight_totals):
            log.warning("iShares %s: deduplicated %d duplicate ticker(s)",
                        product.isin, len(weight_totals) - len(holdings))
        log.info("iShares %s: fetched %d holdings", product.isin, len(holdings))
        return holdings
