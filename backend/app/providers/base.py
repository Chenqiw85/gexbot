from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.domain.gex import OptionType


@dataclass(frozen=True)
class ProviderOptionContract:
    option_symbol: str
    expiration_date: date
    strike: float
    option_type: OptionType
    open_interest: int | None
    volume: int | None
    implied_volatility: float | None
    iv_observed_at: datetime | None
    oi_observed_date: date | None
    contract_multiplier: int | None
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    mark: float | None = None
    vendor_delta: float | None = None
    vendor_gamma: float | None = None
    vendor_theta: float | None = None
    vendor_vega: float | None = None


@dataclass(frozen=True)
class SpotQuote:
    ticker: str
    spot_price: float
    spot_observed_at: datetime | None
    provider_received_at: datetime


class ProviderError(RuntimeError):
    pass


class ProviderDataError(ProviderError):
    pass


class MarketDataProvider:
    source = "base"

    def available_expirations(self, ticker: str) -> list[date]:
        raise NotImplementedError

    def option_chain_for_expiration(self, ticker: str, expiration: date) -> list[ProviderOptionContract]:
        raise NotImplementedError

    def spot_quote(self, ticker: str) -> SpotQuote:
        raise NotImplementedError
