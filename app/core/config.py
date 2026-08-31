"""Application configuration (12-factor, env-driven)."""
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @model_validator(mode="before")
    @classmethod
    def _strip_env_whitespace(cls, data):
        # Railway/.env values often carry a stray trailing space (e.g. "true ").
        # Strip every string input before type coercion so a typo can't crash boot.
        if isinstance(data, dict):
            return {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}
        return data

    # Runtime
    app_name: str = "InvestWise Pro"
    app_version: str = "22.1"
    environment: str = "development"
    debug: bool = True
    auto_create_tables: bool = True

    # Database (async Postgres)
    database_url: str = (
        "postgresql+asyncpg://investwise:investwise@localhost:5432/investwise"
    )

    # Frontend / CORS
    frontend_origin: str = "http://localhost:5173"

    # SuperAdmin
    superadmin_name: str = "Eran Ganot"
    tax_year: int = 2026

    # Tax engine (Section 4.1) -- CONFIRM with accountant; never hardcoded in logic
    cgt_rate: float = 0.25
    surtax_rate: float = 0.05
    surtax_threshold_ils: float = 721_000.0

    # Risk engine (Section 4.4)
    max_drawdown_cap: float = 0.20

    # W2 -- the regime gate's two user-owned numbers.
    #
    # `strategy_backtest.MAX_CAGR_GIVEUP_PCT` has always carried the comment
    # that it is "decided, not discovered", and on this catalog it is decisive:
    # vol_target buys 8.32 points of drawdown for 1.71%/yr and fails ONLY
    # because 1.71 > 1.0. A number that settles a verdict and expresses a
    # preference about money belongs to the user, not to a module.
    regime_gate_max_cagr_giveup_pct: float = 1.0

    # PR 16. Which measured return prices the book's forward numbers:
    # "measured_oos" (default), "measured_full", or "static".
    #
    # The default is the OUT-OF-SAMPLE figure, not the full window, and that is
    # a considered choice rather than caution. W1 walked every overlay forward
    # on 2026-08-27 and NOT ONE reached FITTING_HELPS, so a full-sample CAGR for
    # a 3x leveraged trend strategy measured over 2015-2025 is a bull-market
    # extrapolation. `split_solver` already decides its rank key once per run
    # for the same reason: ranking some candidates out-of-sample and others on
    # the full sample compares two different questions.
    #
    # VOLATILITY HAS NO SETTING. There is no reading under which a static guess
    # about an instrument beats a measurement of the rule actually being run.
    assumption_return_basis: str = "measured_oos"

    # Comma-separated strategy ids whose HEADLINE numbers are measured with the
    # gate ON. Empty by default, and that default is the rule
    # `backtest_service` already states: "the gate ships off everywhere until a
    # human has compared the two numbers." This is how a human says they have.
    #
    # It changes what the stored row MEANS, so `ENGINE_VERSION` moved with it
    # and every row records `regime_gate_applied` -- a gated number that cannot
    # be told from an ungated one is worse than no gate at all.
    regime_gate_enabled_for: str = ""
    volatility_cap: float = 0.15
    monte_carlo_runs: int = 10_000
    risk_horizon_years: float = 1.0
    risk_mc_steps: int = 252  # trading days per year
    ruin_probability_cap: float = 0.20  # veto if >20% of paths breach the drawdown cap
    risk_distribution: str = "normal"   # normal | t (Student-t fat tails)
    risk_t_dof: int = 5                  # degrees of freedom for the t distribution
    lag_min_divergence_pct: float = 2.0  # Lag noise floor; smaller divergences ignored
    # Fall back to demo_data.DEFAULT_OBSERVATIONS when no real signal is available.
    # Off in production: sample prices must never be presented as real advice.
    demo_signals: bool = False
    sim_cpi_pct: float = 3.0          # default annual inflation (CPI) for projections
    sim_fx_change_pct: float = 0.0    # default annual FX drift for projections
    concentration_cap: float = 0.25   # max single-position weight before concentration flag
    min_liquidity_ratio: float = 0.05 # min liquid/cash ratio before liquidity flag
    api_key: str = ""                 # if set, write endpoints require X-API-Key header
    allocation_drift_threshold: float = 0.03   # |drift| above this triggers a rebalance action
    rebalance_txn_cost_bps: float = 10.0       # transaction cost in basis points
    rebalance_slippage_bps: float = 5.0        # slippage in basis points
    rebalance_assumed_gain_ratio: float = 0.30 # fraction of a SELL assumed to be taxable gain
    market_data_provider: str = "builtin"      # builtin | yahoo | fmp (DIP-selected)
    fmp_api_key: str = ""                       # Financial Modeling Prep key; set + MARKET_DATA_PROVIDER=fmp for real fundamentals
    base_currency: str = "ILS"                  # portfolio reporting currency; holdings are FX-converted to this
    fx_provider: str = "builtin"
    provider_rate_limit_per_sec: float = 20.0
    provider_cb_failure_threshold: int = 5
    provider_cb_recovery_sec: float = 30.0
    provider_cache_ttl_sec: float = 15.0
    # Quotes go stale in seconds; fundamentals and price history do not. One
    # shared 15s TTL meant /recommendations refetched ~18 payloads on every
    # request (6 holdings x fundamentals twice + 200-day history), costing 24s
    # of sequential network latency. Separate TTLs by how fast the data actually
    # moves: daily bars for an hour, quarterly fundamentals for six.
    provider_history_cache_ttl_sec: float = 3600.0        # daily bars
    provider_fundamentals_cache_ttl_sec: float = 21600.0  # quarterly filings
    # HOW LONG AN EXPIRED ANSWER STAYS BETTER THAN NO ANSWER.
    #
    # Serving a stale series is a judgement about the DATA, not about the cache:
    # daily closes do not change between fetches, so a six-hour-old history is
    # the same history. Quotes are the opposite, which is why this is wired only
    # to the history and fundamentals tiers -- a stale PRICE would silently
    # misvalue the book, and that is the one failure this app must not have.
    provider_history_stale_ttl_sec: float = 21600.0       # 6h of emergency stock
    provider_fundamentals_stale_ttl_sec: float = 86400.0  # 24h
    # A failed key is remembered this long, so callers queued behind it inherit
    # the error instead of each sending their own request upstream.
    provider_failure_cooloff_sec: float = 20.0
    redis_url: str = ""             # if set, Celery uses Redis; else runs eager (synchronous)
    enable_scheduler: bool = True     # start APScheduler cron jobs in-process (hourly market refresh)
    # Google sign-in (Phase A)
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = "https://investwise-pro-production.up.railway.app/auth/google/callback"
    allowed_emails: str = "eran.ganot@gmail.com"   # CSV allowlist; only these may sign in
    session_cookie_name: str = "iw_session"
    post_login_redirect: str = "/app"
    require_auth: bool = False        # if True, JWT + RBAC enforced on protected routes
    session_ttl_sec: int = 2592000    # "remember me": Google session lasts 30 days
    agent_api_key: str = ""           # if set, X-Agent-Key header grants SUPERADMIN write access (for the agent)
    superadmin_email: str = "eran.ganot@gmail.com"
    # NO DEFAULT, deliberately. A default here is a PUBLISHED credential rather
    # than a password: everyone with repo access has it, and this repository was
    # public until 2026-08-30. The placeholder that stood on this line from the
    # first commit was still accepted by production that day -- POST
    # /api/v1/auth/token returned 200 and a SUPERADMIN token. It is named in
    # auth_service.PUBLISHED_PASSWORDS, which now refuses it, and nowhere else.
    # Blank means password login is unavailable rather than open.
    auth_password: str = ""           # SuperAdmin login password; set AUTH_PASSWORD in the environment
    jwt_secret: str = ""               # if set, HS256 with this secret -> stable sessions across redeploys
    jwt_private_key: str = ""          # RS256 PEM; generated ephemerally if blank
    jwt_public_key: str = ""
    sentry_dsn: str = ""               # if set (and sentry-sdk installed), error tracking is enabled
    access_token_ttl_sec: int = 900
    refresh_token_ttl_sec: int = 1209600
    m2m_token_ttl_sec: int = 31536000

    # Portfolio risk (Phase D)
    benchmark_ticker: str = "SPY"   # benchmark for portfolio beta

    # Historical backtesting (Phase 3.3) - validate the Risk Agent's beta
    backtest_beta_tolerance: float = 0.25   # flag if vol-implied beta diverges from structural beta by more
    backtest_market_vol_pct: float = 16.0   # broad-market annual volatility used for the implied-beta calc

    # Fee optimizer (Phase 3.2)
    fee_high_threshold_pct: float = 0.50    # flag holdings whose expense ratio exceeds this %

    # Brokerage / aggregation (Phase 3.1) - holdings sync
    broker_enabled: bool = False            # gate real providers (plaid/yodlee); mock always works
    aggregator_provider: str = "mock"       # mock | plaid | yodlee

    # Adversary agent (Section 6 / Phase 1.3) - per-stage cross-examination
    adversary_enabled: bool = True          # route every stage through the Adversary examiners
    adversary_enforce_veto: bool = True     # a BLOCK-severity finding becomes a hard veto
    adversary_llm_enabled: bool = False     # optional LLM narrative on top of deterministic checks (needs GOOGLE_API_KEY)
    adversary_llm_model: str = "gemini-2.0-flash"  # any Gemini model you have access to

    # Decision engine (Section 4.5) display gates
    min_impact_score: float = 20.0
    min_confidence: float = 60.0
    score_unknown_default: float = 25.0  # sub-score for unassessed dimensions (penalizes incomplete data)
    decision_return_scale: float = 5.0          # maps return/divergence % to the 0-100 return sub-score
    preferred_depth: int | None = None          # plan 'flavor': favor this Lag depth (1/2/3)
    objective: str | None = None                 # plan objective (Grow/Balanced/Preserve/Income) the agents optimize toward
    confidence_dq_base: float = 60.0            # base data-quality score
    confidence_dq_bonus: float = 20.0           # +per available data dimension (risk, tax)
    confidence_model_agreement: float = 80.0    # baseline model agreement
    confidence_conflict_agreement: float = 55.0 # agreement when return/risk conflict
    analytics_vol_risk_factor: float = 2.0      # health: risk_score = 100 - vol% * factor
    analytics_geo_cap: float = 0.80             # geographic concentration alert threshold
    analytics_tax_efficiency_base: float = 85.0 # base tax-efficiency before loss penalty
    rec_ttl_now_days: int = 1
    rec_ttl_week_days: int = 7
    rec_ttl_monitor_days: int = 30

    # Web Push notifications (PWA push)
    # Leave blank to auto-generate + persist a VAPID keypair in the DB on first
    # use. Set these env vars to pin a stable keypair across environments.
    vapid_public_key: str = ""      # base64url raw (applicationServerKey)
    vapid_private_key: str = ""     # base64url raw private key
    vapid_subject: str = "mailto:eran.ganot@gmail.com"  # VAPID 'sub' claim
    push_price_move_pct: float = 5.0           # notify when a holding moves >= this % since last alert
    push_notify_severities: str = "CRITICAL,HIGH"  # rec/alert severities worth a push
    push_dedupe_days: int = 7                  # don't repeat the same notification within N days

    @property
    def allowed_email_list(self) -> list[str]:
        return [e.strip().lower() for e in (self.allowed_emails or "").split(",") if e.strip()]

    @field_validator("database_url")
    @classmethod
    def _force_asyncpg(cls, v: str) -> str:
        """Normalize Railway/Heroku-style URLs to the async driver."""
        if v.startswith("postgresql+asyncpg://"):
            return v
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
