from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, Text, func
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
    added_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
