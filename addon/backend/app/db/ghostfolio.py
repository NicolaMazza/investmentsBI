"""Ghostfolio database adapter — read-only models pinned to Ghostfolio 3.3.0.

Tables live in the `public` schema of the `ghostfolio` database.
Column names mirror Prisma-generated names (camelCase) exactly.

WARNING: verify this file against the live schema before upgrading Ghostfolio.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, Numeric, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class GhostfolioBase(DeclarativeBase):
    pass


class SymbolProfile(GhostfolioBase):
    """Instrument metadata: ISIN, ticker, currency, asset class."""
    __tablename__ = "SymbolProfile"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    dataSource: Mapped[str] = mapped_column(Text, nullable=False)
    isin: Mapped[Optional[str]] = mapped_column(Text)
    name: Mapped[Optional[str]] = mapped_column(Text)
    assetClass: Mapped[Optional[str]] = mapped_column(Text)
    assetSubClass: Mapped[Optional[str]] = mapped_column(Text)


class Account(GhostfolioBase):
    """Brokerage account grouping."""
    __tablename__ = "Account"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    isExcluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Order(GhostfolioBase):
    """Individual transactions (BUY, SELL, DIVIDEND, etc.)."""
    __tablename__ = "Order"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    accountId: Mapped[Optional[str]] = mapped_column(Text)
    date: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(65, 30), nullable=False)
    unitPrice: Mapped[Decimal] = mapped_column(Numeric(65, 30), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(65, 30), nullable=False)
    symbolProfileId: Mapped[str] = mapped_column(Text, nullable=False)
    # type is a Postgres enum in Ghostfolio; read as text (BUY/SELL/DIVIDEND/FEE/ITEM/LIABILITY/STAKE)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    userId: Mapped[str] = mapped_column(Text, nullable=False)


class User(GhostfolioBase):
    """Ghostfolio user — used to auto-detect userId when not configured."""
    __tablename__ = "User"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)  # USER, ADMIN


class MarketData(GhostfolioBase):
    """Historical prices — composite PK (date, symbol, dataSource)."""
    __tablename__ = "MarketData"

    date: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=False), primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    dataSource: Mapped[str] = mapped_column(Text, primary_key=True)
    marketPrice: Mapped[Decimal] = mapped_column(Numeric(65, 30), nullable=False)
