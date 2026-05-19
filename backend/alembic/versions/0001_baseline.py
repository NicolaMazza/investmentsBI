"""baseline: investments_bi schema and product table

Revision ID: 0001
Revises:
Create Date: 2026-05-19
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS product (
            isin            TEXT PRIMARY KEY,
            ticker          TEXT,
            name            TEXT NOT NULL,
            product_type    TEXT NOT NULL CHECK (product_type IN
                                ('etf','stock','bond','mutual_fund','crypto','cash')),
            issuer          TEXT,
            base_currency   TEXT,
            source_url      TEXT,
            parser          TEXT,
            cadence         TEXT CHECK (cadence IN ('daily','monthly','quarterly','static')),
            active          BOOLEAN NOT NULL DEFAULT TRUE,
            added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS product")
