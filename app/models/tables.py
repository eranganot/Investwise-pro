"""All 11 persisted tables (Section 4 of the spec).

Kept in one module so SQLAlchemy can resolve cross-table relationships
without import-ordering or forward-reference friction.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PKMixin, TimestampMixin

MONEY = Numeric(18, 4)


# --- Identity / structure ---------------------------------------------------
class User(Base, PKMixin, TimestampMixin):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50), default="SuperAdmin")
    tax_year: Mapped[int] = mapped_column(Integer, default=2026)

    entities: Mapped[list["Entity"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    decision_feeds: Mapped[list["DecisionFeed"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Entity(Base, PKMixin, TimestampMixin):
    __tablename__ = "entities"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    entity_type: Mapped[str] = mapped_column(String(50))  # Personal|Spouse|Corp

    user: Mapped["User"] = relationship(back_populates="entities")
    accounts: Mapped[list["Account"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )
    buckets: Mapped[list["Bucket"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )


class Account(Base, PKMixin, TimestampMixin):
    __tablename__ = "accounts"
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    broker: Mapped[str | None] = mapped_column(String(255), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="ILS")

    entity: Mapped["Entity"] = relationship(back_populates="accounts")
    positions: Mapped[list["Position"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class Bucket(Base, PKMixin, TimestampMixin):
    __tablename__ = "buckets"
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    objective: Mapped[str] = mapped_column(String(50), default="Growth")  # Growth|Bulletproof

    entity: Mapped["Entity"] = relationship(back_populates="buckets")


# --- Portfolio --------------------------------------------------------------
class Position(Base, PKMixin, TimestampMixin):
    __tablename__ = "positions"
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    bucket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("buckets.id", ondelete="SET NULL"), nullable=True
    )
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[str] = mapped_column(String(16))  # TASE|NYSE|SPOT
    quantity: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    cost_basis: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    current_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    lifecycle_stage: Mapped[str] = mapped_column(String(16), default="DETECTED")
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)

    account: Mapped["Account"] = relationship(back_populates="positions")
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="position", cascade="all, delete-orphan"
    )


class Transaction(Base, PKMixin, TimestampMixin):
    __tablename__ = "transactions"
    position_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), index=True
    )
    txn_type: Mapped[str] = mapped_column(String(16))  # Buy|Sell
    quantity: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    price: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    fees: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    position: Mapped["Position"] = relationship(back_populates="transactions")


# --- Tax --------------------------------------------------------------------
class TaxProfile(Base, PKMixin, TimestampMixin):
    __tablename__ = "tax_profiles"
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    surtax_status: Mapped[bool] = mapped_column(Boolean, default=False)
    loss_carry_forward: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    trapped_profit_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


# --- Decisions / feed -------------------------------------------------------
class DecisionFeed(Base, PKMixin, TimestampMixin):
    __tablename__ = "decision_feeds"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    horizon: Mapped[str] = mapped_column(String(16), default="month")  # month|quarter|year
    status: Mapped[str] = mapped_column(String(16), default="OPEN")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="decision_feeds")
    items: Mapped[list["DecisionItem"]] = relationship(
        back_populates="feed", cascade="all, delete-orphan"
    )


class DecisionItem(Base, PKMixin, TimestampMixin):
    __tablename__ = "decision_items"
    feed_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decision_feeds.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    action_type: Mapped[str] = mapped_column(String(16))  # Buy|Sell|Rebalance|Tax|Risk
    trigger: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    urgency: Mapped[int] = mapped_column(Integer, default=1)
    complexity: Mapped[int] = mapped_column(Integer, default=1)
    time_sensitivity: Mapped[str] = mapped_column(String(16), default="Monitor")
    veto_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_critique: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)  # full output contract

    feed: Mapped["DecisionFeed"] = relationship(back_populates="items")
    actions: Mapped[list["UserAction"]] = relationship(
        back_populates="decision_item", cascade="all, delete-orphan"
    )


class WhsSnapshot(Base, PKMixin, TimestampMixin):
    __tablename__ = "whs_snapshots"
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    score: Mapped[float] = mapped_column(Float, default=0.0)
    risk: Mapped[float] = mapped_column(Float, default=0.0)
    tax: Mapped[float] = mapped_column(Float, default=0.0)
    alloc: Mapped[float] = mapped_column(Float, default=0.0)
    liq: Mapped[float] = mapped_column(Float, default=0.0)
    thematic: Mapped[float] = mapped_column(Float, default=0.0)
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)


# --- Learning loop ----------------------------------------------------------
class UserAction(Base, PKMixin, TimestampMixin):
    __tablename__ = "user_actions"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    decision_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decision_items.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(16))  # accepted|ignored
    acted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    decision_item: Mapped["DecisionItem"] = relationship(back_populates="actions")
