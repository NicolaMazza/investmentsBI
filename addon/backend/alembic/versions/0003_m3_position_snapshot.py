"""M3: fx_rate and position_snapshot tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-21
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS fx_rate (
            as_of_date      DATE    NOT NULL,
            base_currency   TEXT    NOT NULL,
            quote_currency  TEXT    NOT NULL,
            rate            NUMERIC(18, 8) NOT NULL,
            PRIMARY KEY (as_of_date, base_currency, quote_currency)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_fx_rate_lookup
        ON fx_rate (base_currency, quote_currency, as_of_date DESC)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS position_snapshot (
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
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_position_snapshot_date
        ON position_snapshot (as_of_date DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS position_snapshot")
    op.execute("DROP TABLE IF EXISTS fx_rate")
