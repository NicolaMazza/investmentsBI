"""M8: normalise sector labels in existing composition rows.

Unifies iShares 'Information Technology' and Vanguard 'Technology' into
'Technology', and collapses other minor naming variants so the sector
treemap shows one bucket per GICS sector regardless of data source.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-23
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_RENAMES = [
    ("Information Technology", "Technology"),
    ("Financial Services",     "Financials"),
    ("Healthcare",             "Health Care"),
    ("Telecommunications",     "Communication Services"),
    ("Basic Materials",        "Materials"),
]

# Same renames for portfolio_allocation_snapshot (pre-computed rows)
_TABLES = ["product_composition_snapshot", "portfolio_allocation_snapshot"]


def upgrade() -> None:
    for old, new in _RENAMES:
        for table in _TABLES:
            col = "sector" if table == "product_composition_snapshot" else "segment_key"
            lbl = "sector" if table == "product_composition_snapshot" else "segment_label"
            op.execute(f"""
                UPDATE {table}
                SET    {col} = '{new}', {lbl} = '{new}'
                WHERE  {col} = '{old}'
            """) if table == "portfolio_allocation_snapshot" else op.execute(f"""
                UPDATE {table}
                SET    {col} = '{new}'
                WHERE  {col} = '{old}'
            """)


def downgrade() -> None:
    pass  # data-only; no safe rollback
