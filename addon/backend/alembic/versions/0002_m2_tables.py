"""M2: product_composition_snapshot, job_run tables; seed IWDA

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-20
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS product_composition_snapshot (
            as_of_date          DATE        NOT NULL,
            product_isin        TEXT        NOT NULL REFERENCES product(isin),
            constituent_isin    TEXT        NOT NULL,
            constituent_name    TEXT,
            ticker              TEXT,
            weight_pct          NUMERIC(8,5)  NOT NULL,
            sector              TEXT,
            country_listing     TEXT,
            country_incorp      TEXT,
            native_currency     TEXT,
            asset_class         TEXT,
            market_value_native NUMERIC(20,2),
            shares              NUMERIC(20,4),
            PRIMARY KEY (as_of_date, product_isin, constituent_isin)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_pcs_constituent
        ON product_composition_snapshot (constituent_isin, as_of_date)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS job_run (
            id          BIGSERIAL PRIMARY KEY,
            job_name    TEXT        NOT NULL,
            started_at  TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ,
            status      TEXT        NOT NULL
                            CHECK (status IN ('running','ok','failed','partial')),
            rows_written INTEGER,
            message     TEXT
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_job_run_name
        ON job_run (job_name, started_at DESC)
    """)

    # Seed IWDA
    op.execute("""
        INSERT INTO product (isin, ticker, name, product_type, issuer, base_currency,
                             source_url, parser, cadence, active)
        VALUES (
            'IE00B4L5Y983',
            'EUNL.DE',
            'iShares Core MSCI World UCITS ETF (Acc)',
            'etf',
            'ishares',
            'USD',
            'https://www.ishares.com/uk/individual/en/products/251882/ishares-msci-world-ucits-etf-acc-fund/1506575576011.ajax?fileType=csv&fileName=SWDA_holdings&dataType=fund',
            'ishares_csv',
            'daily',
            true
        )
        ON CONFLICT (isin) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM product WHERE isin = 'IE00B4L5Y983'")
    op.execute("DROP TABLE IF EXISTS job_run")
    op.execute("DROP TABLE IF EXISTS product_composition_snapshot")
