from __future__ import annotations

from dataclasses import dataclass
import math
import os

from app.domain.ticker import normalize_ticker


SUPPORTED_PROVIDERS = frozenset({"mock", "tradier"})


@dataclass(frozen=True)
class Settings:
    provider: str
    tradier_base_url: str
    tradier_token: str | None
    default_tickers: tuple[str, ...]
    default_risk_free_rate: float
    default_dividend_yield: float
    default_min_dte: int
    default_max_dte: int
    default_max_expirations: int
    chain_refresh_seconds: int
    spot_refresh_seconds: int
    database_url: str | None
    cors_origins: tuple[str, ...]
    auto_ensure_schema: bool
    db_pool_min_size: int = 1
    db_pool_max_size: int = 5
    db_pool_timeout_seconds: float = 10.0
    api_key: str | None = None
    bind_host: str = "127.0.0.1"


def get_settings() -> Settings:
    provider = _provider("GEXBOT_PROVIDER", "mock")
    tradier_token = _optional_string("TRADIER_TOKEN")
    if provider == "tradier" and tradier_token is None:
        raise ValueError("TRADIER_TOKEN is required when GEXBOT_PROVIDER=tradier")

    default_min_dte = _non_negative_int("DEFAULT_MIN_DTE", "0")
    default_max_dte = _non_negative_int("DEFAULT_MAX_DTE", "45")
    if default_min_dte > default_max_dte:
        raise ValueError("DEFAULT_MIN_DTE must be less than or equal to DEFAULT_MAX_DTE")

    db_pool_min_size = _non_negative_int("DB_POOL_MIN_SIZE", "1")
    db_pool_max_size = _positive_int("DB_POOL_MAX_SIZE", "5")
    if db_pool_min_size > db_pool_max_size:
        raise ValueError("DB_POOL_MIN_SIZE must be less than or equal to DB_POOL_MAX_SIZE")
    db_pool_timeout_seconds = _positive_float("DB_POOL_TIMEOUT_SECONDS", "10")

    return Settings(
        provider=provider,
        tradier_base_url=os.getenv("TRADIER_BASE_URL", "https://api.tradier.com/v1"),
        tradier_token=tradier_token,
        default_tickers=_ticker_csv("GEXBOT_TICKERS", "SPY,QQQ"),
        default_risk_free_rate=_finite_float("DEFAULT_RISK_FREE_RATE", "0.05"),
        default_dividend_yield=_unit_float("DEFAULT_DIVIDEND_YIELD", "0.0"),
        default_min_dte=default_min_dte,
        default_max_dte=default_max_dte,
        default_max_expirations=_positive_int("DEFAULT_MAX_EXPIRATIONS", "12"),
        chain_refresh_seconds=_positive_int("CHAIN_REFRESH_SECONDS", "900"),
        spot_refresh_seconds=_positive_int("SPOT_REFRESH_SECONDS", "15"),
        database_url=os.getenv("DATABASE_URL"),
        cors_origins=_csv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"),
        auto_ensure_schema=_bool("AUTO_ENSURE_SCHEMA", "true"),
        db_pool_min_size=db_pool_min_size,
        db_pool_max_size=db_pool_max_size,
        db_pool_timeout_seconds=db_pool_timeout_seconds,
        api_key=_optional_string("GEXBOT_API_KEY"),
        bind_host=os.getenv("GEXBOT_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1",
    )


def _csv(name: str, default: str) -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _optional_string(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _ticker_csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(normalize_ticker(item) for item in _csv(name, default))


def _provider(name: str, default: str) -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in SUPPORTED_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ValueError(f"{name} must be one of: {supported}")
    return value


def _int(name: str, default: str) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def _finite_float(name: str, default: str) -> float:
    try:
        value = float(os.getenv(name, default))
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _unit_float(name: str, default: str) -> float:
    value = _finite_float(name, default)
    if value < 0 or value > 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


def _positive_int(name: str, default: str) -> int:
    value = _int(name, default)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: str) -> float:
    value = _finite_float(name, default)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _non_negative_int(name: str, default: str) -> int:
    value = _int(name, default)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _bool(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")
