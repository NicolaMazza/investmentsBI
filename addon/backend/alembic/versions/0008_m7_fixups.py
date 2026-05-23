"""M7 fixups: uppercase 2-letter country codes in existing composition rows.

normalize_country was corrected in M7 to uppercase ISO-3166 alpha-2 codes
(e.g. Nl → NL, Gb → GB).  This migration patches the rows already written
by the M6 fetchers so the country dimension is consistent immediately, without
needing to re-run the etf_holdings job.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-23
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Uppercase every 2-letter country_listing value that is currently title-case
    # (e.g. 'Nl' → 'NL', 'Gb' → 'GB').  The SIMILAR TO pattern matches exactly
    # two ASCII letters; the UPPER() call normalises any capitalisation variant.
    op.execute("""
        UPDATE product_composition_snapshot
        SET    country_listing = UPPER(country_listing)
        WHERE  country_listing SIMILAR TO '[A-Za-z]{2}'
          AND  country_listing != UPPER(country_listing)
    """)

    # Same fix for position_snapshot.native_currency just in case any driver
    # stored lowercase/mixed-case currency codes (shouldn't happen but harmless).
    op.execute("""
        UPDATE product_composition_snapshot
        SET    native_currency = UPPER(native_currency)
        WHERE  native_currency IS NOT NULL
          AND  native_currency != UPPER(native_currency)
          AND  length(native_currency) BETWEEN 3 AND 4
    """)


def downgrade() -> None:
    pass  # data-only migration; no safe downgrade
