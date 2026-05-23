"""M7: ensure portfolio_allocation_snapshot and instrument_reference tables exist.

These tables were defined in reporting.py from M5 onwards but may not have
been created by earlier Alembic runs depending on the migration path.  This
migration is idempotent: it only creates the tables if they are missing.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # portfolio_allocation_snapshot — pre-computed daily aggregates (M7)
    op.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_allocation_snapshot (
            as_of_date      DATE    NOT NULL,
            dimension       TEXT    NOT NULL,
            segment_key     TEXT    NOT NULL,
            segment_label   TEXT    NOT NULL,
            value_eur       NUMERIC(20,2) NOT NULL,
            weight_pct      NUMERIC(8,5)  NOT NULL,
            holding_count   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (as_of_date, dimension, segment_key)
        )
    """)

    # instrument_reference — constituent-level reference data (market_cap M7+)
    op.execute("""
        CREATE TABLE IF NOT EXISTS instrument_reference (
            isin                TEXT PRIMARY KEY,
            name                TEXT,
            market_cap_eur      NUMERIC(20,0),
            market_cap_bucket   TEXT,
            last_refreshed_at   TIMESTAMP
        )
    """)

    # country_of_risk_override — manual overrides (M7+)
    op.execute("""
        CREATE TABLE IF NOT EXISTS country_of_risk_override (
            isin        TEXT PRIMARY KEY,
            country     TEXT NOT NULL,
            note        TEXT,
            updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # Index on portfolio_allocation_snapshot for date-range queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_pas_dimension_date
        ON portfolio_allocation_snapshot (dimension, as_of_date DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_pas_dimension_date")
    op.execute("DROP TABLE IF EXISTS portfolio_allocation_snapshot")
    op.execute("DROP TABLE IF EXISTS instrument_reference")
    op.execute("DROP TABLE IF EXISTS country_of_risk_override")
