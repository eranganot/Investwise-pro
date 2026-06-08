"""Application configuration (12-factor, env-driven)."""
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

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
    volatility_cap: float = 0.15
    monte_carlo_runs: int = 10_000
    risk_horizon_years: float = 1.0
    risk_mc_steps: int = 252  # trading days per year
    ruin_probability_cap: float = 0.20  # veto if >20% of paths breach the drawdown cap
    lag_min_divergence_pct: float = 2.0  # Lag noise floor; smaller divergences ignored
    sim_cpi_pct: float = 3.0          # default annual inflation (CPI) for projections
    sim_fx_change_pct: float = 0.0    # default annual FX drift for projections
    concentration_cap: float = 0.25   # max single-position weight before concentration flag
    min_liquidity_ratio: float = 0.05 # min liquid/cash ratio before liquidity flag
    api_key: str = ""                 # if set, write endpoints require X-API-Key header
    allocation_drift_threshold: float = 0.03   # |drift| above this triggers a rebalance action
    rebalance_txn_cost_bps: float = 10.0       # transaction cost in basis points
    rebalance_slippage_bps: float = 5.0        # slippage in basis points
    rebalance_assumed_gain_ratio: float = 0.30 # fraction of a SELL assumed to be taxable gain

    # Decision engine (Section 4.5) display gates
    min_impact_score: float = 20.0
    min_confidence: float = 60.0

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
