from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, Integer, Numeric, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "product"
    __table_args__ = (
        CheckConstraint(
            "product_type IN ('etf','stock','bond','mutual_fund','crypto','cash')",
            name="product_type_check",
        ),
        CheckConstraint(
            "cadence IN ('daily','monthly','quarterly','static')",
            name="cadence_check",
        ),
        {},
    )

    isin: Mapped[str] = mapped_column(Text, primary_key=True)
    ticker: Mapped[Optional[str]] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    product_type: Mapped[str] = mapped_column(Text, nullable=False)
    issuer: Mapped[Optional[str]] = mapped_column(Text)
    base_currency: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    parser: Mapped[Optional[str]] = mapped_column(Text)
    cadence: Mapped[Optional[str]] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    added_at: Mapped[datetime.datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )


class ProductCompositionSnapshot(Base):
    __tablename__ = "product_composition_snapshot"
    __table_args__ = (
        CheckConstraint("weight_pct >= 0", name="weight_pct_nonneg"),
        {},
    )

    as_of_date: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    product_isin: Mapped[str] = mapped_column(Text, primary_key=True)
    constituent_isin: Mapped[str] = mapped_column(Text, primary_key=True)
    constituent_name: Mapped[Optional[str]] = mapped_column(Text)
    ticker: Mapped[Optional[str]] = mapped_column(Text)
    weight_pct: Mapped[float] = mapped_column(Numeric(8, 5), nullable=False)
    sector: Mapped[Optional[str]] = mapped_column(Text)
    country_listing: Mapped[Optional[str]] = mapped_column(Text)
    country_incorp: Mapped[Optional[str]] = mapped_column(Text)
    native_currency: Mapped[Optional[str]] = mapped_column(Text)
    asset_class: Mapped[Optional[str]] = mapped_column(Text)
    market_value_native: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    shares: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))


class FxRate(Base):
    """Daily EUR FX rates sourced from the ECB.

    base_currency is always 'EUR'.  rate = how many quote_currency per 1 EUR
    (ECB convention, e.g. 1 EUR = 1.12 USD → rate=1.12, quote_currency='USD').
    To convert native amount to EUR: eur_value = native_amount / rate.
    """
    __tablename__ = "fx_rate"

    as_of_date: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    base_currency: Mapped[str] = mapped_column(Text, primary_key=True)
    quote_currency: Mapped[str] = mapped_column(Text, primary_key=True)
    rate: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)


class PositionSnapshot(Base):
    """Net position per account × symbol as of a given date.

    Quantities are computed from Ghostfolio Orders (BUY − SELL).
    Market values use the latest available price from Ghostfolio MarketData;
    EUR conversion uses same-date (or nearest prior) fx_rate rows.
    """
    __tablename__ = "position_snapshot"

    as_of_date: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol_profile_id: Mapped[str] = mapped_column(Text, primary_key=True)
    isin: Mapped[Optional[str]] = mapped_column(Text)
    symbol: Mapped[Optional[str]] = mapped_column(Text)
    name: Mapped[Optional[str]] = mapped_column(Text)
    currency: Mapped[Optional[str]] = mapped_column(Text)
    quantity: Mapped[float] = mapped_column(Numeric(30, 8), nullable=False)
    market_price_native: Mapped[Optional[float]] = mapped_column(Numeric(20, 4))
    market_value_native: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))
    fx_rate_to_eur: Mapped[Optional[float]] = mapped_column(Numeric(18, 8))
    market_value_eur: Mapped[Optional[float]] = mapped_column(Numeric(20, 2))


class JobRun(Base):
    __tablename__ = "job_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','ok','failed','partial')", name="job_run_status_check"
        ),
        {},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(nullable=False)
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column()
    status: Mapped[str] = mapped_column(Text, nullable=False)
    rows_written: Mapped[Optional[int]] = mapped_column(Integer)
    message: Mapped[Optional[str]] = mapped_column(Text)
