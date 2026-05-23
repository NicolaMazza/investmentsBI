"""Seed the local dev DB with representative positions + trigger all jobs.

Run once after `docker compose -f docker-compose.dev.yml up`:

    docker compose -f docker-compose.dev.yml exec backend python dev_seed.py

What this does:
  1. Inserts product rows for the three ETFs (iShares MSCI World, Vanguard
     FTSE Developed Europe, HSBC MSCI Emerging Markets).
  2. Inserts a PositionSnapshot for today with representative EUR values.
  3. Triggers ishares_holdings + etf_holdings to fetch real composition data.
  4. Triggers aggregate_allocation to pre-compute the dashboard rows.
  5. Triggers the backfill endpoint (90d) so the drift chart has data.

After this runs, open http://localhost:8000 — all pivots should be live.

Note: etf_holdings launches Playwright (Vanguard) which requires Chromium.
The dev Dockerfile does NOT install Playwright, so that job will error for
VWCG but will still succeed for HSBC (plain HTTP).  Run via the full HA
add-on if you need Vanguard data locally.
"""
import datetime
import sys

# Make sure we can import app modules
sys.path.insert(0, "/app")

from app.db.reporting import PositionSnapshot, Product
from app.db.reporting_session import SessionLocal
from app.fetchers.base import load_all_fetchers
from app.jobs import aggregate_allocation, etf_holdings, ishares_holdings

TODAY = datetime.date.today()

PRODUCTS = [
    dict(
        isin="IE00B4L5Y983", ticker="IWDA", name="iShares Core MSCI World UCITS ETF",
        product_type="etf", issuer="ishares", base_currency="USD",
        source_url="https://www.ishares.com/uk/individual/en/products/251882/ISHARES_MSCI_WORLD_UCITS_ETF/1478372549651.ajax?fileType=json&fileName=IWDA_holdings&dataType=fund",
        parser="ishares_json", cadence="daily", active=True,
    ),
    dict(
        isin="IE00BK5BQX27", ticker="VWCG", name="Vanguard FTSE Developed Europe UCITS ETF (Acc)",
        product_type="etf", issuer="vanguard", base_currency="EUR",
        source_url="https://www.vanguard.co.uk/professional/product/etf/equity/9681/ftse-developed-europe-ucits-etf-eur-accumulating",
        parser="vanguard_xlsx", cadence="monthly", active=True,
    ),
    dict(
        isin="IE000KCS7J59", ticker="H4Z3", name="HSBC MSCI Emerging Markets UCITS ETF",
        product_type="etf", issuer="hsbc", base_currency="USD",
        source_url="https://www.assetmanagement.hsbc.co.uk/api/v1/download/document/ie000kcs7j59/gb/en/holdings",
        parser="hsbc_xlsx", cadence="monthly", active=True,
    ),
]

# Approximate EUR values — adjust to match your real portfolio
POSITIONS = [
    dict(isin="IE00B4L5Y983", quantity=100,  market_value_eur=25_000, native_currency="USD"),
    dict(isin="IE00BK5BQX27", quantity=200,  market_value_eur=12_000, native_currency="EUR"),
    dict(isin="IE000KCS7J59", quantity=500,  market_value_eur= 8_000, native_currency="USD"),
]


def seed_db() -> None:
    session = SessionLocal()
    try:
        # Upsert products
        for p in PRODUCTS:
            existing = session.get(Product, p["isin"])
            if existing:
                for k, v in p.items():
                    setattr(existing, k, v)
            else:
                session.add(Product(**p))

        # Upsert today's position snapshot
        for pos in POSITIONS:
            existing = session.get(PositionSnapshot, (TODAY, pos["isin"]))
            if existing:
                existing.market_value_eur = pos["market_value_eur"]
            else:
                session.add(PositionSnapshot(
                    as_of_date=TODAY,
                    product_isin=pos["isin"],
                    quantity=pos["quantity"],
                    market_value_eur=pos["market_value_eur"],
                    native_currency=pos["native_currency"],
                ))
        session.commit()
        print(f"✓ Seeded {len(PRODUCTS)} products and {len(POSITIONS)} positions for {TODAY}")
    finally:
        session.close()


def run_jobs() -> None:
    load_all_fetchers()
    print("Running ishares_holdings…")
    try:
        ishares_holdings.run()
        print("  ✓ ishares_holdings done")
    except Exception as e:
        print(f"  ✗ ishares_holdings: {e}")

    print("Running etf_holdings (HSBC only in dev — Vanguard needs Playwright)…")
    try:
        etf_holdings.run()
        print("  ✓ etf_holdings done")
    except Exception as e:
        print(f"  ✗ etf_holdings: {e}")

    print("Running aggregate_allocation…")
    try:
        aggregate_allocation.run()
        print("  ✓ aggregate_allocation done")
    except Exception as e:
        print(f"  ✗ aggregate_allocation: {e}")


def backfill() -> None:
    import httpx
    try:
        r = httpx.post("http://localhost:8000/api/admin/backfill?days=90", timeout=10)
        data = r.json()
        print(f"✓ Backfill: {data.get('rows_written')} rows ({data.get('from_date')} → {data.get('to_date')})")
    except Exception as e:
        print(f"  ✗ backfill via API: {e} — run manually from admin panel")


if __name__ == "__main__":
    print("=== InvestmentsBI dev seed ===")
    seed_db()
    run_jobs()
    backfill()
    print("\nDone — open http://localhost:8000")
