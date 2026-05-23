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

Column mapping (Vanguard FTSE Developed Europe UCITS ETF, IE00BK5BQX27)
------------------------------------------------------------------------
    "Holding name"        → constituent_name
    "ISIN"                → constituent_isin  (preferred)
    "SEDOL"               → constituent_isin  (fallback, with warning)
    "Ticker"              → constituent_isin  (last-resort fallback)
    "% of market value"   → weight_pct  (stored as "3.8514%", % stripped)
    "Region"              → country_listing   (ISO-2 country code, e.g. "NL")
    "Market value"        → market_value_native  (€-prefixed, symbol stripped)
    "Shares"              → shares
    "Sector"              → sector  (GICS sector string, populated when present)
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

_WEIGHT_COLS   = ["% of market value", "% of fund net assets", "Percentage of fund",
                  "Weighting", "Weighting (%)", "% of Fund", "% Net Assets"]
_NAME_COLS     = ["Holding name", "Name", "Security name", "Stock name"]
_COUNTRY_COLS  = ["Region", "Country of domicile", "Country of risk", "Country", "Domicile"]
_CURRENCY_COLS = ["Currency", "Local currency", "Dealing currency", "Ccy"]
_SHARES_COLS   = ["Shares", "Quantity", "Number of shares", "Nominal"]
_MV_COLS       = ["Market value", "Market value (local currency)", "Market value (EUR)",
                  "Market value (GBP)", "Market Value"]
_SECTOR_COLS   = ["Sector", "GICS Sector", "ICB Sector", "Industry"]
_TICKER_COLS   = ["Ticker", "Ticker symbol", "Bloomberg ticker"]

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

        # Dismiss cookie banners and professional-investor confirmation modals.
        # Run multiple passes — some sites show a cookie banner then a second modal.
        _dismiss_popups(page)

        # Scroll down gradually to trigger lazy-loaded sections.
        for _ in range(6):
            page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
            page.wait_for_timeout(600)

        # Scroll back to top so Holdings section selector can find the element.
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)

        # Wait for the Holdings section — try several text variants.
        holdings_found = False
        for selector in [
            "text=/holdings details/i",
            "text=/holdings/i",
            "text=/portfolio holdings/i",
        ]:
            try:
                page.wait_for_selector(selector, timeout=15_000, state="visible")
                holdings_found = True
                log.debug("Vanguard %s: found section via selector '%s'", isin, selector)
                break
            except PWTimeout:
                pass

        if not holdings_found:
            log.warning(
                "Vanguard %s: no holdings section found — attempting download anyway",
                isin,
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            page.context.set_default_timeout(_DOWNLOAD_TIMEOUT)

            try:
                with page.expect_download(timeout=_DOWNLOAD_TIMEOUT) as dl_info:
                    result = _click_holdings_download(page)
                    log.debug("Vanguard %s: click result: %s", isin, result)
            except PWTimeout:
                raise RuntimeError(
                    f"Vanguard {isin}: download did not start within "
                    f"{_DOWNLOAD_TIMEOUT // 1000}s. "
                    "Check add-on logs for the list of buttons found on the page."
                )

            download = dl_info.value
            dest = pathlib.Path(tmpdir) / "holdings.xlsx"
            download.save_as(str(dest))
            log.info("Vanguard %s: downloaded %d bytes", isin, dest.stat().st_size)
            return dest.read_bytes()

    # ── XLSX parsing ──────────────────────────────────────────────────────────

    def _parse_xlsx(self, isin: str, raw: bytes) -> list[NormalizedHolding]:
        buf = io.BytesIO(raw)

        # Vanguard workbooks are multi-sheet: sheet 1 contains only download
        # metadata, holdings are on a later sheet.  Scan all sheets for the
        # one that contains a cell with "holding name".
        xl = pd.ExcelFile(buf, engine="openpyxl")
        log.debug("Vanguard %s: sheets in workbook: %s", isin, xl.sheet_names)

        header_row: int | None = None
        target_sheet: str | int | None = None

        for sheet_name in xl.sheet_names:
            probe = xl.parse(sheet_name, header=None)
            for idx, row in probe.iterrows():
                for cell in row.values:
                    if isinstance(cell, str) and "holding name" in cell.lower():
                        header_row = int(str(idx))
                        target_sheet = sheet_name
                        break
                if header_row is not None:
                    break
            if header_row is not None:
                log.debug(
                    "Vanguard %s: found header row %d on sheet '%s'",
                    isin, header_row, target_sheet,
                )
                break

        if header_row is None or target_sheet is None:
            # Report what was found on each sheet to aid debugging.
            sheet_previews = []
            for sn in xl.sheet_names:
                try:
                    first = xl.parse(sn, header=None).iloc[0].tolist()
                except Exception:
                    first = ["<empty>"]
                sheet_previews.append(f"  {sn!r}: {first}")
            raise ValueError(
                f"Vanguard XLSX for {isin}: could not find header row "
                "(expected a cell containing 'Holding name') on any sheet.\n"
                + "\n".join(sheet_previews)
            )

        buf.seek(0)
        df = pd.read_excel(buf, sheet_name=target_sheet, header=header_row, engine="openpyxl")
        df.columns = df.columns.str.strip()
        df = df.dropna(how="all")
        log.debug("Vanguard %s columns: %s", isin, df.columns.tolist())

        weight_col   = _pick(df, _WEIGHT_COLS)
        name_col     = _pick(df, _NAME_COLS)
        country_col  = _pick(df, _COUNTRY_COLS)
        currency_col = _pick(df, _CURRENCY_COLS)
        shares_col   = _pick(df, _SHARES_COLS)
        mv_col       = _pick(df, _MV_COLS)
        sector_col   = _pick(df, _SECTOR_COLS)

        if weight_col is None:
            raise ValueError(
                f"Vanguard XLSX for {isin}: no weight column found. "
                f"Columns: {df.columns.tolist()}"
            )

        # Prefer ISIN, fall back to SEDOL, then Ticker.
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
            ticker_col = _pick(df, _TICKER_COLS)
            if ticker_col:
                id_col = ticker_col
                log.warning(
                    "Vanguard %s: no ISIN or SEDOL column — using Ticker '%s' as "
                    "constituent identifier; look-through joins will be by ticker.",
                    isin, ticker_col,
                )
            else:
                raise ValueError(
                    f"Vanguard XLSX for {isin}: no ISIN, SEDOL, or Ticker column. "
                    f"Columns: {df.columns.tolist()}"
                )

        df = df.dropna(subset=[id_col])
        df = df[~df[id_col].astype(str).str.strip().isin(["", "nan", "None"])]

        def _safe_float(val: object) -> float | None:
            if val is None:
                return None
            try:
                # Strip currency symbols (€, £, $, etc.) and percent signs
                cleaned = (
                    str(val)
                    .replace(",", "")
                    .replace("%", "")
                    .strip()
                    .lstrip("€£$¥₹")
                    .strip()
                )
                result = float(cleaned)
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

            sector: str | None = None
            if sector_col:
                raw_sector = str(row[sector_col]).strip()
                if raw_sector not in ("nan", "None", ""):
                    sector = raw_sector

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
                    sector=sector,
                )
            )

        log.info("Vanguard %s: parsed %d holdings", isin, len(holdings))
        return holdings


# ── page helpers ──────────────────────────────────────────────────────────────

def _dismiss_popups(page: object) -> None:  # type: ignore[override]
    """Click through cookie banners and professional-investor confirmation modals.

    Runs up to 3 passes so that sequential popups (e.g. cookie banner → PI
    declaration) are each dismissed in turn.
    """
    from playwright.sync_api import Page, TimeoutError as PWTimeout

    assert isinstance(page, Page)

    # Ordered by likelihood — Vanguard professional pages show a cookie banner
    # first, then a "professional investors only" confirmation modal.
    popup_selectors = [
        # Cookie / GDPR banners
        "button:has-text('Accept all')",
        "button:has-text('Accept cookies')",
        "button:has-text('Accept')",
        # Vanguard UK professional investor gate
        "button:has-text('I understand')",
        "button:has-text('I confirm')",
        "button:has-text('Confirm')",
        "button:has-text('I am a professional')",
        "button:has-text('Yes, I am a professional')",
        "button:has-text('Continue')",
        "button:has-text('Proceed')",
        # Generic fallbacks
        "[id*='accept'] button",
        "[class*='accept'] button",
        "[class*='cookie'] button",
    ]

    for _pass in range(3):
        dismissed = False
        for sel in popup_selectors:
            try:
                btn = page.wait_for_selector(sel, timeout=3_000, state="visible")
                if btn:
                    btn.click()
                    page.wait_for_timeout(800)
                    log.debug("Dismissed popup: %s", sel)
                    dismissed = True
                    break
            except PWTimeout:
                pass
        if not dismissed:
            break  # No more popups


def _click_holdings_download(page: object) -> str:  # type: ignore[override]
    """Click the Download button in the Holdings Details section.

    Returns a debug string describing which button was clicked (or what was
    found on the page) — logged at DEBUG level by the caller.
    """
    from playwright.sync_api import Page

    assert isinstance(page, Page)

    result: str = page.evaluate("""() => {
        // Collect all clickable elements whose visible text includes 'download'
        const all = [...document.querySelectorAll('button, a, [role="button"]')];
        const dlBtns = all.filter(b => {
            const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
            return txt.includes('download');
        });

        // Log every candidate for debugging
        const summary = dlBtns.map((b, i) => {
            const s = b.closest('section, article, [class*="panel"], [class*="card"], [class*="module"]');
            return `[${i}] text="${(b.innerText||'').trim().substring(0,40)}" section="${(s?.className||'').substring(0,60)}"`;
        }).join(' | ');

        if (dlBtns.length === 0) {
            // Report all button texts to help diagnose the page structure
            const allTxt = all.map(b => (b.innerText||b.textContent||'').trim())
                              .filter(t => t.length > 0 && t.length < 40)
                              .slice(0, 30);
            return `NO DOWNLOAD BUTTONS FOUND. All button texts: ${JSON.stringify(allTxt)}`;
        }

        // Exclude price-history download buttons (text matches 'download NNN prices').
        const holdingsBtns = dlBtns.filter(b => {
            const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
            return !(/download\\s+\\d+\\s+prices?/i.test(txt)) && !txt.includes('price');
        });
        const candidates = holdingsBtns.length > 0 ? holdingsBtns : dlBtns;

        // Strategy 1: find the button whose nearest container mentions 'holdings'
        for (const btn of candidates) {
            const section = btn.closest('section, article, div');
            if (section) {
                const txt = (section.innerText || section.textContent || '');
                if (/holdings/i.test(txt)) {
                    btn.scrollIntoView({ behavior: 'instant', block: 'center' });
                    btn.click();
                    return `clicked holdings-section button. Candidates: ${summary}`;
                }
            }
        }

        // Strategy 2: first non-price Download button
        const first = candidates[0];
        first.scrollIntoView({ behavior: 'instant', block: 'center' });
        first.click();
        return `clicked first non-price button (fallback). Candidates: ${summary}`;
    }""")

    if result.startswith("NO DOWNLOAD BUTTONS FOUND"):
        raise RuntimeError(
            f"Could not find a Download button on the Vanguard page. {result}"
        )

    return result
