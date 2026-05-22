"""M5: align position_snapshot with design schema; seed VWCG & H4Z3;
add portfolio_allocation_snapshot, instrument_reference,
country_of_risk_override.

IMPORTANT: This migration DROPS the M3 position_snapshot table (schema
change from account×sp_id PK to product_isin PK).  The table is fully
regeneratable — re-run the position_snapshot job once after the upgrade.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-22
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Seed VWCG and H4Z3 ────────────────────────────────────────────────
    # Must be done before creating the new position_snapshot FK.
    op.execute("""
        INSERT INTO product (isin, ticker, name, product_type, issuer,
                             base_currency, source_url, parser, cadence, active)
        VALUES (
            'IE00BK5BQX27', 'VWCG.DE',
            'Vanguard FTSE Developed Europe UCITS ETF (Acc)',
            'etf', 'vanguard', 'EUR',
            NULL, 'vanguard_xlsx', 'monthly', true
        )
        ON CONFLICT (isin) DO NOTHING
    """)
    op.execute("""
        INSERT INTO product (isin, ticker, name, product_type, issuer,
                             base_currency, source_url, parser, cadence, active)
        VALUES (
            'IE000KCS7J59', 'H4Z3.DE',
            'HSBC MSCI Emerging Markets UCITS ETF (Acc)',
            'etf', 'hsbc', 'USD',
            NULL, 'hsbc_xlsx', 'monthly', true
        )
        ON CONFLICT (isin) DO NOTHING
    """)

    # ── 2. Replace position_snapshot (schema break) ───────────────────────────
    # Old PK: (as_of_date, account_id, symbol_profile_id)
    # New PK: (as_of_date, product_isin) — one row per product per day.
    op.execute("DROP TABLE IF EXISTS position_snapshot")
    op.execute("""
        CREATE TABLE position_snapshot (
            as_of_date          DATE            NOT NULL,
            product_isin        TEXT            NOT NULL REFERENCES product(isin),
            quantity            NUMERIC(20, 4)  NOT NULL,
            market_value_native NUMERIC(20, 2),
            native_currency     TEXT,
            market_value_eur    NUMERIC(20, 2),
            cost_basis_eur      NUMERIC(20, 2),
            PRIMARY KEY (as_of_date, product_isin)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_position_snapshot_date
        ON position_snapshot (as_of_date DESC)
    """)

    # ── 3. portfolio_allocation_snapshot ─────────────────────────────────────
    # Pre-computed daily aggregates per (date, dimension, segment).
    # Populated by the aggregate_allocation scheduler job (M7).
    # Allocation API reads from here once that job runs; falls back to
    # on-the-fly computation when the table is empty.
    op.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_allocation_snapshot (
            as_of_date      DATE            NOT NULL,
            dimension       TEXT            NOT NULL,
            segment_key     TEXT            NOT NULL,
            segment_label   TEXT            NOT NULL,
            value_eur       NUMERIC(20, 2)  NOT NULL,
            weight_pct      NUMERIC(8, 5)   NOT NULL,
            holding_count   INTEGER         NOT NULL DEFAULT 0,
            PRIMARY KEY (as_of_date, dimension, segment_key)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_pas_dimension_date
        ON portfolio_allocation_snapshot (dimension, as_of_date DESC)
    """)

    # ── 4. instrument_reference ───────────────────────────────────────────────
    # Constituent-level reference data enriched weekly by market_cap job (M7).
    op.execute("""
        CREATE TABLE IF NOT EXISTS instrument_reference (
            isin                TEXT PRIMARY KEY,
            name                TEXT,
            market_cap_eur      NUMERIC(20, 0),
            market_cap_bucket   TEXT CHECK (market_cap_bucket IN
                                    ('Mega','Large','Mid','Small','Micro','Unknown')),
            last_refreshed_at   TIMESTAMPTZ
        )
    """)

    # ── 5. country_of_risk_override ───────────────────────────────────────────
    # Manual per-ISIN country-of-risk overrides (admin endpoint in M7).
    op.execute("""
        CREATE TABLE IF NOT EXISTS country_of_risk_override (
            isin        TEXT PRIMARY KEY,
            country     TEXT NOT NULL,
            note        TEXT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS country_of_risk_override")
    op.execute("DROP TABLE IF EXISTS instrument_reference")
    op.execute("DROP TABLE IF EXISTS portfolio_allocation_snapshot")

    # Restore M3 position_snapshot schema
    op.execute("DROP TABLE IF EXISTS position_snapshot")
    op.execute("""
        CREATE TABLE position_snapshot (
            as_of_date          DATE            NOT NULL,
            account_id          TEXT            NOT NULL,
            symbol_profile_id   TEXT            NOT NULL,
            isin                TEXT,
            symbol              TEXT,
            name                TEXT,
            currency            TEXT,
            quantity            NUMERIC(30, 8)  NOT NULL,
            market_price_native NUMERIC(20, 4),
            market_value_native NUMERIC(20, 2),
            fx_rate_to_eur      NUMERIC(18, 8),
            market_value_eur    NUMERIC(20, 2),
            PRIMARY KEY (as_of_date, account_id, symbol_profile_id)
        )
    """)

    op.execute("DELETE FROM product WHERE isin IN ('IE00BK5BQX27', 'IE000KCS7J59')")
