"""All 11 persisted tables (Section 4 of the spec).

Kept in one module so SQLAlchemy can resolve cross-table relationships
without import-ordering or forward-reference friction.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PKMixin, TimestampMixin

MONEY = Numeric(18, 4)
# The scale the database actually keeps, read off the column rather than
# repeated as a literal, so a change to MONEY cannot leave writers behind.
_MONEY_QUANT = Decimal(1).scaleb(-(MONEY.scale or 4))


def money(value) -> Decimal:
    """Quantise a value to the precision the MONEY column will actually keep.

    `Decimal(str(x))` preserves every digit it is handed; `Numeric(18, 4)` does
    not. Postgres rounds on write, so the row that comes back is not the row
    that went in -- while the identity-mapped ORM object keeps the
    full-precision value until it is expired. An invariant that reads the ORM
    object therefore measures a number the database will never return, which is
    how a conservation check can pass over a book that did move.

    Measured on Postgres: writing 4.712276 shares reads back 4.7123, and
    `_rebalance_to` writes six decimal places on every leg -- ~0.12 ILS of
    unattributed NAV per accept on a 35k book, scaling with share price and
    position count. SQLite keeps all six digits and shows nothing, which is why
    only `test-postgres` could ever have seen it.

    Every writer of a MONEY column goes through here, so what is computed is
    what is stored, on both databases. ROUND_HALF_EVEN is what Postgres does,
    so the two agree exactly rather than approximately.
    """
    return Decimal(str(value)).quantize(_MONEY_QUANT, rounding=ROUND_HALF_EVEN)
# Postgres uses native JSONB; SQLite (local dev) falls back to generic JSON.
JSONB_OR_JSON = JSONB().with_variant(JSON(), "sqlite")


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
    meta: Mapped[dict] = mapped_column(JSONB_OR_JSON, default=dict)

    account: Mapped["Account"] = relationship(back_populates="positions")
    # NO delete-orphan. The ORM cascade deletes children in Python whatever the
    # FK says, so `SET NULL` on transactions.position_id would have been quietly
    # overruled and selling out of a holding would still have erased the record
    # of the sale. `passive_deletes` hands the decision to the database, which is
    # the only place it can be enforced consistently.
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="position", cascade="save-update, merge",
        passive_deletes=True,
    )


class Transaction(Base, PKMixin, TimestampMixin):
    """What the money actually did. One row per leg, written by `ledger_service`.

    **This table had zero writers for the entire life of the app** -- nothing
    outside the models ever constructed a `Transaction`. So `nav_history` had no
    flow data, every book change read as market performance, and the accepts on
    2026-08-21 that inflated NAV by 7,388 left no trace whatsoever. Phase N's own
    docstring recorded that as the reason past NAV could not be reconstructed.

    Two schema problems had to be fixed before it could be written to:

    * ``position_id`` was NOT NULL with ``ondelete="CASCADE"``. A full exit
      deletes the position -- and would have taken the record of the sale with
      it, so the ledger erased precisely the events it exists to remember. Now
      nullable with SET NULL, and the identifying fields are denormalised so a
      row still says what it was once its position is gone.
    * There was nowhere to put the ILS value, the tax, or the cause. A ledger
      that cannot be reconciled against NAV is a list, not a ledger.
    """

    __tablename__ = "transactions"
    position_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("positions.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # Denormalised on purpose: the row must outlive the position it describes.
    subject: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    ticker: Mapped[str | None] = mapped_column(String(24), index=True, nullable=True)
    market: Mapped[str | None] = mapped_column(String(16), nullable=True)
    txn_type: Mapped[str] = mapped_column(String(16))  # Buy|Sell|Adjust
    quantity: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    price: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    fees: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    # ILS is the base currency everywhere else; the ledger has to agree with NAV
    # or it cannot be reconciled against it.
    value_ils: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    tax_ils: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    # What caused this leg: the accept kind, or "reconcile".
    source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
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
    data: Mapped[dict] = mapped_column(JSONB_OR_JSON, default=dict)


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
    payload: Mapped[dict] = mapped_column(JSONB_OR_JSON, default=dict)  # full output contract

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
    detail: Mapped[dict] = mapped_column(JSONB_OR_JSON, default=dict)


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


# --- Auth (Section AC) ---
class Credential(Base, PKMixin, TimestampMixin):
    __tablename__ = "credentials"
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="READ_ONLY")


class RevokedToken(Base, PKMixin, TimestampMixin):
    __tablename__ = "revoked_tokens"
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)


class AuditLog(Base, PKMixin, TimestampMixin):
    __tablename__ = "audit_log"
    method: Mapped[str] = mapped_column(String(8))
    route: Mapped[str] = mapped_column(String(255))
    origin_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="open")
    payload_sha256: Mapped[str] = mapped_column(String(64))


class Plan(Base, PKMixin, TimestampMixin):
    __tablename__ = "plans"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    objective: Mapped[str] = mapped_column(String(16), default="Balanced")        # Grow|Balanced|Preserve|Income
    risk_tolerance: Mapped[str] = mapped_column(String(8), default="Medium")      # Low|Medium|High
    horizon_years: Mapped[int] = mapped_column(Integer, default=10)
    target_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    target_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="ILS")
    target_roi_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_roi_period: Mapped[str] = mapped_column(String(12), default="yearly")
    target_yield_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_yield_period: Mapped[str] = mapped_column(String(12), default="yearly")
    preferred_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Dry powder, as a PERCENTAGE like every other `_pct` column here: 7.5 means
    # 7.5% of NAV held deliberately in cash so there is something to buy an
    # opportunity with. NULL means nobody has decided, which is not the same as
    # a deliberate zero -- `book_target` treats both as no reserve, but the UI
    # can tell the two apart.
    #
    # Taken out of the CORE's share of the book and never out of the sleeves.
    # See `book_target.compose`, where that decision is argued.
    cash_target_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # THE SAME TARGET IN SHEKELS, and it wins when set. Eran, 2026-09-21: "the
    # card should appear if I have more than 1500, not by % of my portfolio."
    # A fixed amount of dry powder does not grow with the book; a percentage
    # does. NULL means "use cash_target_pct". Readers never branch on this
    # themselves: `funding_service.cash_floor_pct(..., nav=)` and
    # `cash_floor_ils` are the only places it is interpreted.
    cash_target_ils: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    # WHEN the put-cash-to-work card appears, separate from how much it keeps.
    # Eran, 2026-09-21: the card appears above 1,500 and invests down to the
    # 150 hard floor. Between the two, no card either way -- a band, so the
    # card does not reappear the moment a single deposit lands. NULL: the card
    # fires as soon as there is a tradeable amount above the cash target.
    cash_redeploy_at_ils: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    # `strategy` and `strategy_sleeve_pct` lived here and are GONE. One id and
    # one size can describe exactly one sleeve, so the moment a book could run
    # several they became a pointer to the most recently applied -- and a
    # pointer that nothing kept in step. On the live book the day they were
    # removed they read "btm_swing_dip at 15%" while `plan_sleeves` held
    # btm_swing_dip at 38.4% and btm_trend_soxl at 25.6%: two answers to one
    # question, the older wrong by 23 points and still being read by the tax
    # harvester, the signal service and the drift card as a fallback.
    # `plan_sleeves` is the only source now. See migration 0019.


# --- Brokerage / aggregation (Phase 3.1 scaffold) ---------------------------
class BrokerConnection(Base, PKMixin, TimestampMixin):
    """A user's link to an external brokerage/aggregator. Credentials are never
    stored here - only an encrypted ``credential_ref`` pointing at a secret."""
    __tablename__ = "broker_connections"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))          # mock|plaid|yodlee
    external_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    institution: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="CONNECTED")  # CONNECTED|ERROR|DISCONNECTED
    credential_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# --- Web Push (notifications) ----------------------------------------------
class PushSubscription(Base, PKMixin, TimestampMixin):
    """A browser Web Push subscription. Keyed by the acting principal's subject
    (email in auth mode, SuperAdmin name in open mode) so we can fan out to all
    of a user's installed devices."""
    __tablename__ = "push_subscriptions"
    subject: Mapped[str] = mapped_column(String(255), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(Text)
    auth: Mapped[str] = mapped_column(Text)
    ua: Mapped[str | None] = mapped_column(String(255), nullable=True)


class NotifiedEvent(Base, PKMixin, TimestampMixin):
    """Dedupe ledger so a trigger fires a given notification at most once per
    window (keyed by subject + a stable signature of the event)."""
    __tablename__ = "notified_events"
    subject: Mapped[str] = mapped_column(String(255), index=True)
    signature: Mapped[str] = mapped_column(String(160), index=True)


class KVSetting(Base, TimestampMixin):
    """Tiny key/value store for runtime secrets generated lazily (e.g. the VAPID
    keypair) so push works after deploy without manual env setup."""
    __tablename__ = "kv_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class TradingRule(Base, PKMixin, TimestampMixin):
    """A user-defined alert/discipline rule on a holding.

    A trigger never moves anything on its own: it records a RuleEvent, notifies,
    and surfaces an executable card. Executing it updates the *tracked* book only
    -- InvestWise places no broker orders."""
    __tablename__ = "trading_rules"
    subject: Mapped[str] = mapped_column(String(255), index=True)   # user email
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    # stop_loss | take_profit | trailing_stop | price_above | price_below
    # | buy_dip | max_weight | strategy_signal
    rule_type: Mapped[str] = mapped_column(String(24))
    mode: Mapped[str] = mapped_column(String(8), default="pct")     # pct | price | entry | exit
    level: Mapped[float] = mapped_column(Float)
    # Which strategy a `strategy_signal` rule follows, pinned at arm time.
    # Without pinning, changing the applied strategy would silently repoint every
    # armed entry/exit rule at a different set of trades -- the user would still
    # see "TQQQ entry" and be following something they never agreed to.
    strategy_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    peak_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # trailing-stop high-water
    # What fraction of the position ONE take-profit firing sells. NULL is not
    # zero: null means the house default (`rules_service.TAKE_PROFIT_FRACTION`,
    # 0.25); zero would mean a rule that sells nothing. Only `take_profit` reads
    # it -- a stop-loss and a trailing stop are full exits by definition.
    trim_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)
    # PR 80: the advanced rules' second settings (activation gain, held-back
    # tickers...). Null = house defaults. See alembic 0025_rule_params.
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    note: Mapped[str | None] = mapped_column(String(160), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class RuleEvent(Base, PKMixin, TimestampMixin):
    """The life story of a trading rule: it fired, and here's what happened next.

    Previously a firing left only ``triggered`` + ``last_triggered_at`` on the
    rule itself, so 'the rule triggered' was visible but 'what was done about it'
    was not recorded anywhere. Each firing writes one row; executing or
    dismissing the resulting card updates that same row in place.
    """
    __tablename__ = "rule_events"
    subject: Mapped[str] = mapped_column(String(255), index=True)   # user email
    rule_id: Mapped[str] = mapped_column(String(64), index=True)
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    rule_type: Mapped[str] = mapped_column(String(24))
    # What the market did to fire it (grounded, never back-filled).
    trigger_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    # triggered -> executed | dismissed | expired
    outcome: Mapped[str] = mapped_column(String(16), default="triggered", index=True)
    outcome_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # What actually changed in the tracked book (shares, proceeds, tax) -- empty
    # until the user executes, so "nothing was done" stays legible as nothing.
    action: Mapped[dict] = mapped_column(JSONB_OR_JSON, default=dict)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


class Contribution(Base, PKMixin, TimestampMixin):
    """External money in and out -- the only thing that changes "what you put in".

    ``invested_ils`` used to be derived from the current book: the sum of every
    position's ``cost_basis``, FX-converted at today's rate. That number moves
    for reasons that are not deposits -- the shekel moves, a sale replaces an
    original basis with net-of-CGT proceeds (so taking a profit *raises* "you
    put in"), a fee swap re-stamps basis at the live price. Reported live:
    ₪20,000 deposited, ₪20,790 displayed.

    Trading rearranges money you already have; only a deposit or a withdrawal
    changes how much of your own money is in the account. This ledger records
    exactly those events and nothing else, so gain, gain % and the goal-gap
    projection all rest on a figure that only you can change.

    Deliberately user-scoped by email rather than hung off a position: a sale
    deletes its position and cascades away its ``Transaction`` rows, which is
    precisely how the old history got lost.
    """
    __tablename__ = "contributions"
    subject: Mapped[str] = mapped_column(String(255), index=True)   # user email
    # Signed, base-currency (ILS). Withdrawals are stored negative so the ledger
    # sums directly and a withdrawal can never be double-counted as a deposit.
    amount_ils: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    kind: Mapped[str] = mapped_column(String(16), default="deposit")  # deposit|withdrawal
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    note: Mapped[str] = mapped_column(String(200), default="")


class StandingOrder(Base, PKMixin, TimestampMixin):
    """A recurring monthly deposit into cash -- the user's own standing order.

    The app has no broker link, so this does not MOVE money: on the day, it
    records the deposit the user's bank makes. Each run writes a normal
    ``contributions`` row (so "what you put in" and the NAV-flow detector both
    see a deposit, never a return) and credits the cash row, in one commit.

    ``next_run_on`` is advanced in the SAME commit as the credit, and the
    contribution note carries the order id and the date it is for, so a job that
    runs twice -- or two workers that race -- cannot credit one month twice.
    Dates are YYYY-MM-DD strings, the convention ``nav_snapshots.as_of`` uses.
    """
    __tablename__ = "standing_orders"
    subject: Mapped[str] = mapped_column(String(255), index=True)   # user email
    amount_ils: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    # 1..28 only: every month has a 28th, so "the 31st" never has to mean
    # something different in February.
    day_of_month: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_on: Mapped[str] = mapped_column(String(10), index=True)
    last_run_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(200), default="")


class StrategyBacktest(Base, PKMixin, TimestampMixin):
    """One measured result per strategy, recomputed nightly and served from here.

    Backtesting a strategy means pulling ten years of daily closes for every
    ticker it references and simulating the rule over them. Doing that inside
    the ``/strategies`` request would put a network fan-out on a page load and
    make a strategy list depend on a price provider being up.

    The row carries what produced it -- engine version, data source, the exact
    date span and observation count -- so a figure on a card can always be
    traced back to the run that produced it. ``computed_at`` is the freshness
    check: a stale row renders as stale rather than passing itself off as a
    current measurement, and a run that abstained stores its reason instead of
    leaving the previous number in place to look like a fresh one.
    """
    __tablename__ = "strategy_backtests"
    strategy_id: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    engine_version: Mapped[str] = mapped_column(String(16), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    # Populated when ok is False -- INSUFFICIENT_HISTORY, MISSING_TICKER, ...
    reason: Mapped[str] = mapped_column(String(32), default="")
    detail: Mapped[str] = mapped_column(String(255), default="")
    # The whole measured payload (cagr, drawdown, per-trade stats, exposure).
    metrics: Mapped[dict] = mapped_column(JSONB_OR_JSON, default=dict)
    # Parameter sensitivity + the fit/test split, so fragility travels with the
    # headline number instead of being computed once and forgotten.
    robustness: Mapped[dict] = mapped_column(JSONB_OR_JSON, default=dict)
    data_source: Mapped[str] = mapped_column(String(32), default="")
    # Which benchmark the excess figures in `metrics` are relative to. An excess
    # only means anything against the thing it was measured against, so a change
    # to settings.benchmark_ticker must invalidate the row rather than let a
    # SPY-measured number render under a QQQ label. Empty on rows written before
    # engine a4; those are already stale by version.
    benchmark_ticker: Mapped[str] = mapped_column(String(16), default="")
    period_start: Mapped[str] = mapped_column(String(10), default="")
    period_end: Mapped[str] = mapped_column(String(10), default="")
    observations: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    # A failed refresh is recorded WITHOUT discarding the last good measurement:
    # "no longer measurable" and "the price feed was down for a minute" are
    # different failures, and only the first justifies losing the numbers.
    last_error: Mapped[str] = mapped_column(String(255), default="")
    last_error_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class PlanSleeve(Base, PKMixin, TimestampMixin):
    """One strategy sleeve and the share of the book it governs.

    ``plans.strategy`` + ``plans.strategy_sleeve_pct`` can express exactly one
    strategy, which is why applying a second one overwrote the first and why the
    app looked like it had two strategies when it had one. A sleeve is a row
    here instead, so the book can run 20% ``btm_trend_tqqq`` alongside 15%
    ``btm_factor_stack`` without either pretending to be the whole portfolio.

    **The core is the implicit remainder, not a row.** Whatever the sleeves do
    not claim stays objective-managed exactly as it is today, so the sleeves sum
    to <= 100 rather than to exactly 100. ``is_core`` ships unused, reserved for
    the other model (a static family as an explicit core row summing to 100) so
    that choice does not cost a second migration if it is taken later.

    Keyed by ``subject`` (the user's email) to match ``TradingRule`` and
    ``StrategySignalState``, which is what the per-sleeve rule and signal code
    already joins on. One row per strategy per subject is enforced: two rows for
    the same strategy at two sizes is the ambiguity the single-column design was
    replacing, reintroduced at N scale.
    """
    __tablename__ = "plan_sleeves"
    __table_args__ = (UniqueConstraint("subject", "strategy_id",
                                       name="uq_plan_sleeves_subject_strategy"),)
    subject: Mapped[str] = mapped_column(String(255), index=True)   # user email
    strategy_id: Mapped[str] = mapped_column(String(40), index=True)
    # Share of NAV this sleeve governs, in whole-ish percent (20.0 == 20%), the
    # same unit and meaning as the plans.strategy_sleeve_pct it replaces.
    sleeve_pct: Mapped[float] = mapped_column(Float, default=0.0)
    # RESERVED, always False in C1. See the class docstring: the core is the
    # implicit remainder today. Nothing reads this yet, and nothing should start
    # reading it until the explicit-core model is actually chosen.
    is_core: Mapped[bool] = mapped_column(Boolean, default=False)


class NavSnapshot(Base, PKMixin, TimestampMixin):
    """What the book was worth on one day. The only source of real history.

    Everything historical before this table was a BACKFILL -- today's holdings
    priced back through their own past, which answers "what would this book have
    done", not "what did my money do". Past NAV cannot be recovered: the
    `transactions` table has never been written (grep for `Transaction(` outside
    the models returns nothing), so there is no trade ledger to reconstruct
    from. History can only start.

    ``invested_ils`` is the contributions ledger total AS OF that day, stored
    beside the value on purpose: a NAV that jumped because money arrived is not
    a return, and without the deposit total on the same row a later reader
    cannot tell the two apart. The return arithmetic lives in
    ``services/nav_history.time_weighted``.

    One row per subject per day (``as_of`` is a YYYY-MM-DD string, matching the
    convention ``strategy_backtests.period_start`` already uses), so a job that
    runs twice updates rather than duplicating.
    """
    __tablename__ = "nav_snapshots"
    __table_args__ = (UniqueConstraint("subject", "as_of",
                                       name="uq_nav_snapshots_subject_day"),)
    subject: Mapped[str] = mapped_column(String(255), index=True)   # user email
    as_of: Mapped[str] = mapped_column(String(10), index=True)      # YYYY-MM-DD
    nav_ils: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    cash_ils: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    # Contributions-to-date. The flow detector; see the class docstring.
    invested_ils: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    positions: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="job")  # job|manual
    engine_version: Mapped[str] = mapped_column(String(16), default="")


class StrategySignalState(Base, PKMixin, TimestampMixin):
    """What the user's active rule-based strategy said it should hold, last time.

    A trend or swing rule produces a target every single session, and almost
    every session that target is the same as yesterday's. Only a *change* is
    news, so the previous state has to be stored somewhere -- without it the
    daily job either says nothing useful or notifies the user every day that
    the rule still wants what it wanted yesterday.

    Stores the target as a plain ticker->weight map rather than a rendered
    sentence, so a change is detected by comparing what would be held, not by
    diffing prose that might be reworded later.
    """
    __tablename__ = "strategy_signal_state"
    subject: Mapped[str] = mapped_column(String(255), index=True)   # user email
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[dict] = mapped_column(JSONB_OR_JSON, default=dict)
    # The session the target is derived from -- so a stale price feed cannot be
    # mistaken for a fresh signal.
    as_of: Mapped[str] = mapped_column(String(10), default="")
    # Set when the target changed, cleared once the user acts or dismisses. The
    # card and the push both key off this rather than off the target itself.
    flipped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    previous_target: Mapped[dict] = mapped_column(JSONB_OR_JSON, default=dict)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


class SolverVerdict(Base, PKMixin, TimestampMixin):
    """What the target solver last concluded, so something else can read it.

    T5 asks the war-room agents to treat the solver's answer as a CONSTRAINT.
    They could not, and not for want of a function call: nothing anywhere stored
    a verdict. `GET /plan/target` recomputed from scratch on every request --
    a coarse-then-fine grid sweep, each point one simulation of ten years of
    daily closes. The war room already costs 6,726 ms on /recommendations while
    every other agent comes in under 50 ms, so calling the solver from inside it
    would have put a multi-second sweep on the slowest path in the app.

    A stored verdict decouples the two: the solver writes when it runs (either
    because someone opened the target card, or because the nightly job ran) and
    every reader gets the last answer plus its age. Age is not a footnote --
    a verdict is a statement about a specific book, and this one is allowed to
    BLOCK a card, so a reader that cannot say how old it is has no business
    acting on it.

    Only the fields a constraint needs are columns; the rest of the solver's
    output lands in ``detail``. Denormalising the whole result would make this
    table a second copy of something that is recomputed anyway -- the exact
    failure PR 8 removed one layer down.
    """
    __tablename__ = "solver_verdicts"

    subject: Mapped[str] = mapped_column(String(255), index=True)   # user email
    solver_version: Mapped[str] = mapped_column(String(16), default="")
    # REACHED | REACHED_ABOVE_CAP | DRAWDOWN_BOUND | UNREACHABLE | NOT_MEASURABLE
    outcome: Mapped[str] = mapped_column(String(32), index=True, default="")
    target_excess_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The sleeve size the solver says the target needs, on the fine grid.
    solved_total_sleeve_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # THE ONE THAT MATTERS MOST. True means the book breaches its own ceiling at
    # ZERO sleeve -- i.e. the core alone is the problem and no sleeve size,
    # including none at all, repairs it. It is why "change the core" is a card.
    floor_breaches_ceiling: Mapped[bool] = mapped_column(Boolean, default=False)
    # Why it stopped: concentration_cap | drawdown_ceiling | component_excess.
    binding_constraint: Mapped[str] = mapped_column(String(32), default="")
    # How the verdict was produced, so a stale one can be attributed:
    # "on_demand" (someone opened the target card) | "scheduled".
    source: Mapped[str] = mapped_column(String(16), default="on_demand")
    detail: Mapped[dict] = mapped_column(JSONB_OR_JSON, default=dict)


class PlanApplication(Base, PKMixin, TimestampMixin):
    """Every automated change to the tracked book, and what it replaced.

    Phase A is the first thing in the T line that writes. The rule it relaxes --
    "read-only, without exception" -- existed because a return target one tap
    from a book change is the C5 slider bug with higher stakes. This table is
    half of what makes the relaxation safe: an application that cannot be
    reversed is one the user has to be certain about BEFORE pressing, and
    nobody is.

    ``before_state`` and ``after_state`` are ``{strategy_id: sleeve_pct}``
    snapshots, not diffs. A diff needs a base to be meaningful, and the base is
    exactly what a later reader will not have.

    ``context`` carries what the user was answering when they accepted -- the
    target excess, the ceiling, the solver version -- so a size on the book can
    be traced back to the question that produced it. Without it, "why is this
    sleeve 39%" is unanswerable a month later.

    **Nothing here records a trade.** No order is placed by any path that writes
    this table; these are intended percentages, not executions.
    """
    __tablename__ = "plan_applications"

    subject: Mapped[str] = mapped_column(String(255), index=True)   # user email
    action: Mapped[str] = mapped_column(String(16), default="apply")  # apply | undo
    before_state: Mapped[dict] = mapped_column(JSONB_OR_JSON, default=dict)
    after_state: Mapped[dict] = mapped_column(JSONB_OR_JSON, default=dict)
    context: Mapped[dict] = mapped_column(JSONB_OR_JSON, default=dict)
    allocated_pct_after: Mapped[float] = mapped_column(Float, default=0.0)
    apply_version: Mapped[str] = mapped_column(String(16), default="")
    note: Mapped[str] = mapped_column(Text, default="")
