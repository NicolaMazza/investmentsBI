"""M6: set source_url for HSBC H4Z3 (confirmed) and fx_rate table.

The HSBC holdings URL was confirmed live:
  https://www.assetmanagement.hsbc.co.uk/api/v1/download/document/ie000kcs7j59/gb/en/holdings

The Vanguard VWCG URL is NOT set here — it must be captured from the browser's
DevTools once and then inserted manually (or via a future migration):
  UPDATE product SET source_url = '<url>' WHERE isin = 'IE00BK5BQX27';
See addon/backend/app/fetchers/vanguard.py docstring for step-by-step instructions.

Also creates the fx_rate table if it was not created in M3.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-23
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. HSBC source URL (direct XLSX download) ─────────────────────────────
    op.execute("""
        UPDATE product
        SET source_url = 'https://www.assetmanagement.hsbc.co.uk/api/v1/download/document/ie000kcs7j59/gb/en/holdings'
        WHERE isin = 'IE000KCS7J59'
          AND source_url IS NULL
    """)

    # ── 2. Vanguard source URL (product page — Playwright drives the download) ─
    # source_url is the investor page URL, not a direct file URL.
    # The VanguardFetcher uses Playwright to navigate here and click Download.
    op.execute("""
        UPDATE product
        SET source_url = 'https://www.vanguardinvestor.co.uk/investments/vanguard-ftse-developed-europe-ucits-etf-eur-accumulating'
        WHERE isin = 'IE00BK5BQX27'
          AND source_url IS NULL
    """)

    # ── 2. fx_rate table (created in M3 run.sh but ensure it exists) ──────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS fx_rate (
            as_of_date      DATE            NOT NULL,
            currency_code   TEXT            NOT NULL,
            rate_to_eur     NUMERIC(20, 8)  NOT NULL,
            PRIMARY KEY (as_of_date, currency_code)
        )
    """)

    # ── 3. job_run table (ensure it exists — created in M2) ───────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS job_run (
            id            BIGSERIAL PRIMARY KEY,
            job_name      TEXT        NOT NULL,
            started_at    TIMESTAMPTZ NOT NULL,
            finished_at   TIMESTAMPTZ,
            status        TEXT        NOT NULL CHECK (status IN ('running','ok','failed','partial')),
            rows_written  INTEGER,
            message       TEXT
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_job_run_name_started
        ON job_run (job_name, started_at DESC)
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE product
        SET source_url = NULL
        WHERE isin IN ('IE000KCS7J59', 'IE00BK5BQX27')
    """)
