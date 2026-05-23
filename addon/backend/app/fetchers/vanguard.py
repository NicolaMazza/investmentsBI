"""Vanguard holdings fetcher — drives the product page with a headless browser.

Why Playwright
--------------
Vanguard generates the holdings XLSX entirely in JavaScript (client-side),
so there is no stable download URL to request directly.  A headless Chromium
navigates to the product page, waits for the Holdings Details section to
become interactive, and triggers the Download button.  Playwright's
expect_download() captures the file exactly as a real browser would.

source_url
----------
Set source_url to the Vanguard *investor* page URL for the fund, e.g.:
  https://www.vanguardinvestor.co.uk/investments/vanguard-ftse-developed-europe-ucits-etf-eur-accumulating

This is set automatically by migration 0005 for VWCG (IE00BK5BQX27).

Docker requirement
------------------
The Dockerfile installs Playwright and its Chromium binaries (~290 MB):
  RUN pip install playwright && playwright install --with-deps chromium

Column mapping (Vanguard FTSE Developed Europe UCITS ETF)
---------------------------------------------------------
    "Holding name"           → constituent_name
    "ISIN"                   → constituent_isin  (preferred)
    "SEDOL"                  → constituent_isin  (fallback, with warning)
    "% of fund net assets"   → weight_pct  (already in %)
    "Country of domicile"    → country_listing
    "Currency"               → native_currency
    "Market value (...)"     → market_value_native
    "Quantity" / "Shares"    → shares

Sector note
-----------
Vanguard UCITS XLSX files do not include a sector column.  Constituents will
show as 'Unknown' for the sector dimension.
"""
from __future__ import annotations

import io
import logging
import pathlib
import tempfile
from typing import TYPE_CHECKING

import pandas as pd

from app.fetchers.base import (
    BaseFetcher,
    NormalizedHolding,
    normalize_country,
    normalize_currency,
    register,
)

if TYPE_CHECKING:
    from app.db.reporting import Product

log = logging.getLogger(__name__)

_WEIGHT_COLS   = ["% of fund net assets", "Percentage of fund", "Weighting",
                  "Weighting (%)", "% of Fund", "% Net Assets"]
_NAME_COLS     = ["Holding name", "Name", "Security name", "Stock name"]
_COUNTRY_COLS  = ["Country of domicile", "Country of risk", "Country", "Domicile"]
_CURRENCY_COLS = ["Currency", "Local currency", "Dealing currency", "Ccy"]
_SHARES_COLS   = ["Quantity", "Number of shares", "Shares", "Nominal"]
_MV_COLS       = ["Market value (local currency)", "Market value (EUR)",
                  "Market value (GBP)", "Market value", "Market Value"]

# How long (ms) to wait for the Holdings section and the download to complete.
_PAGE_TIMEOUT     = 60_000
_DOWNLOAD_TIMEOUT = 60_000


def _pick(df: pd.DataFrame, candidates: list[str]) -> str | None:
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
                "Set it to the Vanguard investor page URL for this fund, e.g. "
                "https://www.vanguardinvestor.co.uk/investments/<fund-slug>"
            )

        raw = self._download_via_browser(product.isin, product.source_url)
        return self._parse_xlsx(product.isin, raw)

    # ── browser automation ────────────────────────────────────────────────────

    def _download_via_browser(self, isin: str, page_url: str) -> bytes:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "playwright is not installed.  "
                "Ensure the Dockerfile runs: "
                "pip install playwright && playwright install --with-deps chromium"
            )

        log.info("Vanguard %s: launching headless browser for %s", isin, page_url)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                accept_downloads=True,
            )
            page = context.new_page()

            try:
                raw = self._fetch_page(page, isin, page_url)
            finally:
                browser.close()

        return raw

    def _fetch_page(self, page: object, isin: str, page_url: str) -> bytes:  # type: ignore[override]
        from playwright.sync_api import Page, TimeoutError as PWTimeout

        assert isinstance(page, Page)

        page.goto(page_url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT)

        # Dismiss professional investor / cookie / terms popups if present.
        _dismiss_popups(page)

        # Wait until the Holdings Details section is visible.
        try:
            page.wait_for_selector(
                "text=Holdings details",
                timeout=_PAGE_TIMEOUT,
                state="visible",
            )
        except PWTimeout:
            log.warning(
                "Vanguard %s: 'Holdings details' not found after %ds — "
                "proceeding anyway",
                isin, _PAGE_TIMEOUT // 1000,
            )

        # Scroll the Holdings section into view and click its Download button.
        # There are multiple Download buttons on the page; we want the one
        # associated with Holdings Details, which is the last one rendered.
        with tempfile.TemporaryDirectory() as tmpdir:
            page.context.set_default_timeout(_DOWNLOAD_TIMEOUT)

            try:
                with page.expect_download(timeout=_DOWNLOAD_TIMEOUT) as dl_info:
                    _click_holdings_download(page)
            except PWTimeout:
                raise RuntimeError(
                    f"Vanguard {isin}: download did not start within "
                    f"{_DOWNLOAD_TIMEOUT // 1000}s — the page structure may "
                    "have changed; check the Holdings Details section manually."
                )

            download = dl_info.value
            dest = pathlib.Path(tmpdir) / "holdings.xlsx"
            download.save_as(str(dest))
            log.info("Vanguard %s: downloaded %d bytes", isin, dest.stat().st_size)
            return dest.read_bytes()

    # ── XLSX parsing ──────────────────────────────────────────────────────────

    def _parse_xlsx(self, isin: str, raw: bytes) -> list[NormalizedHolding]:
        buf = io.BytesIO(raw)

        # Find header row: scan for the cell containing "holding name".
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
                f"Vanguard XLSX for {isin}: could not find header row "
                "(expected a cell containing 'Holding name').  "
                f"First row: {probe.iloc[0].tolist()}"
            )

        buf.seek(0)
        df = pd.read_excel(buf, header=header_row, engine="openpyxl")
        df.columns = df.columns.str.strip()
        df = df.dropna(how="all")
        log.debug("Vanguard %s columns: %s", isin, df.columns.tolist())

        weight_col   = _pick(df, _WEIGHT_COLS)
        name_col     = _pick(df, _NAME_COLS)
        country_col  = _pick(df, _COUNTRY_COLS)
        currency_col = _pick(df, _CURRENCY_COLS)
        shares_col   = _pick(df, _SHARES_COLS)
        mv_col       = _pick(df, _MV_COLS)

        if weight_col is None:
            raise ValueError(
                f"Vanguard XLSX for {isin}: no weight column found. "
                f"Columns: {df.columns.tolist()}"
            )

        if "ISIN" in df.columns:
            id_col = "ISIN"
        elif "SEDOL" in df.columns:
            id_col = "SEDOL"
            log.warning(
                "Vanguard %s: no ISIN column — using SEDOL as constituent "
                "identifier; cross-product look-through joins may be unreliable.",
                isin,
            )
        else:
            raise ValueError(
                f"Vanguard XLSX for {isin}: no ISIN or SEDOL column. "
                f"Columns: {df.columns.tolist()}"
            )

        df = df.dropna(subset=[id_col])
        df = df[~df[id_col].astype(str).str.strip().isin(["", "nan", "None"])]

        def _safe_float(val: object) -> float | None:
            if val is None:
                return None
            try:
                result = float(str(val).replace(",", "").replace("%", "").strip())
                return result or None
            except (ValueError, TypeError):
                return None

        holdings: list[NormalizedHolding] = []
        for _, row in df.iterrows():
            identifier = str(row[id_col]).strip()
            if not identifier or identifier.lower() in ("nan", "none"):
                continue

            name = str(row[name_col]).strip() if name_col else None
            if name in ("nan", "None", ""):
                name = None

            holdings.append(
                NormalizedHolding(
                    constituent_isin=identifier,
                    constituent_name=name,
                    weight_pct=_safe_float(row[weight_col]) or 0.0,
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

        log.info("Vanguard %s: parsed %d holdings", isin, len(holdings))
        return holdings


# ── page helpers ──────────────────────────────────────────────────────────────

def _dismiss_popups(page: object) -> None:  # type: ignore[override]
    """Click through any professional-investor / cookie / terms modals."""
    from playwright.sync_api import Page, TimeoutError as PWTimeout

    assert isinstance(page, Page)

    popup_selectors = [
        # Vanguard UK professional investor confirmation
        "button:has-text('I understand')",
        "button:has-text('I confirm')",
        "button:has-text('Confirm')",
        "button:has-text('Accept all')",
        "button:has-text('Accept cookies')",
        # Generic GDPR / cookie banners
        "[id*='accept'] button",
        "[class*='accept'] button",
    ]
    for sel in popup_selectors:
        try:
            btn = page.wait_for_selector(sel, timeout=3_000, state="visible")
            if btn:
                btn.click()
                log.debug("Dismissed popup: %s", sel)
        except PWTimeout:
            pass  # Not present — continue


def _click_holdings_download(page: object) -> None:  # type: ignore[override]
    """Click the Download button in the Holdings Details section.

    The page has several Download buttons (market allocation, holdings, etc.).
    We want the one whose nearest section/container heading mentions 'holdings'.
    Fallback: the last Download button on the page (holdings is always last).
    """
    from playwright.sync_api import Page

    assert isinstance(page, Page)

    # Strategy 1: find a button/link whose parent section contains "Holdings"
    found = page.evaluate("""() => {
        const buttons = [...document.querySelectorAll('button, a')];
        const dlBtns = buttons.filter(b =>
            b.innerText.trim().toLowerCase() === 'download'
        );
        for (const btn of dlBtns) {
            const section = btn.closest('section, article, div[class*="panel"], div[class*="card"], div[class*="holdings"]');
            if (section && /holdings/i.test(section.innerText)) {
                btn.scrollIntoView();
                btn.click();
                return true;
            }
        }
        // Fallback: last Download button on page (Holdings Details is last)
        if (dlBtns.length > 0) {
            const last = dlBtns[dlBtns.length - 1];
            last.scrollIntoView();
            last.click();
            return true;
        }
        return false;
    }""")

    if not found:
        raise RuntimeError(
            "Could not find a Download button on the Vanguard holdings page. "
            "The page structure may have changed."
        )
