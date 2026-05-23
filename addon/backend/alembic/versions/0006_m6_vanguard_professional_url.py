"""M6 fix: switch VWCG source_url to the professional page.

The retail investor page (vanguardinvestor.co.uk) does not expose a Holdings
Details section.  The professional page (vanguard.co.uk/professional/...)
does — this is the one the user originally found the Download button on.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-23
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_PROFESSIONAL_URL = (
    "https://www.vanguard.co.uk/professional/product/etf/equity/9681/"
    "ftse-developed-europe-ucits-etf-eur-accumulating"
)
_RETAIL_URL = (
    "https://www.vanguardinvestor.co.uk/investments/"
    "vanguard-ftse-developed-europe-ucits-etf-eur-accumulating"
)


def upgrade() -> None:
    op.execute(f"""
        UPDATE product
        SET source_url = '{_PROFESSIONAL_URL}'
        WHERE isin = 'IE00BK5BQX27'
    """)


def downgrade() -> None:
    op.execute(f"""
        UPDATE product
        SET source_url = '{_RETAIL_URL}'
        WHERE isin = 'IE00BK5BQX27'
    """)
