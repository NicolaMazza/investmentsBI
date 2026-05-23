"""M8: normalise sector labels in existing composition rows.

Unifies iShares 'Information Technology' and Vanguard 'Technology' into
'Technology', and collapses other minor naming variants so the sector
treemap shows one bucket per GICS sector regardless of data source.

For portfolio_allocation_snapshot the PK includes segment_key, so if both
the old and new name already exist for the same (date, dimension) we delete
the old row before renaming — the new-name row already has the right data.

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


def upgrade() -> None:
    for old, new in _RENAMES:
        # product_composition_snapshot: sector is not part of the PK, plain UPDATE
        op.execute(f"""
            UPDATE product_composition_snapshot
            SET    sector = '{new}'
            WHERE  sector = '{old}'
        """)

        # portfolio_allocation_snapshot: segment_key IS the PK — handle conflicts:
        # 1. Where both old and new already exist → delete old (new already correct)
        # 2. Where only old exists → rename it
        op.execute(f"""
            DELETE FROM portfolio_allocation_snapshot a
            USING  portfolio_allocation_snapshot b
            WHERE  a.as_of_date   = b.as_of_date
              AND  a.dimension    = b.dimension
              AND  a.segment_key  = '{old}'
              AND  b.segment_key  = '{new}'
        """)
        op.execute(f"""
            UPDATE portfolio_allocation_snapshot
            SET    segment_key   = '{new}',
                   segment_label = '{new}'
            WHERE  segment_key   = '{old}'
        """)


def downgrade() -> None:
    pass  # data-only; no safe rollback
