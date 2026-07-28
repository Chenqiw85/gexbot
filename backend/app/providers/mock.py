from __future__ import annotations

from datetime import date, datetime

from app.domain.gex import OptionType
from app.providers.base import MarketDataProvider, ProviderOptionContract, SpotQuote


class MockMarketDataProvider(MarketDataProvider):
    source = "mock"

    def __init__(
        self,
        *,
        now: datetime,
        failing_expirations: set[date] | None = None,
    ) -> None:
        self.now = now
        self.failing_expirations = failing_expirations or set()

    def available_expirations(self, ticker: str) -> list[date]:
        return [
            date(2026, 7, 27),
            date(2026, 7, 31),
            date(2026, 8, 7),
            date(2026, 9, 18),
        ]

    def option_chain_for_expiration(self, ticker: str, expiration: date) -> list[ProviderOptionContract]:
        if expiration in self.failing_expirations:
            raise RuntimeError(f"mock failure for {expiration.isoformat()}")

        ticker = ticker.upper()
        contracts: list[ProviderOptionContract] = []
        for strike in (480.0, 490.0, 500.0, 510.0, 520.0):
            for option_type in (OptionType.CALL, OptionType.PUT):
                contracts.append(
                    ProviderOptionContract(
                        option_symbol=_option_symbol(ticker, expiration, option_type, strike),
                        expiration_date=expiration,
                        strike=strike,
                        option_type=option_type,
                        open_interest=100 + int(abs(strike - 500)),
                        volume=10 + int(abs(strike - 500) / 10),
                        implied_volatility=0.20 + abs(strike - 500) / 1000,
                        iv_observed_at=None,
                        oi_observed_date=None,
                        contract_multiplier=100,
                        bid=1.0,
                        ask=1.2,
                        last=1.1,
                        mark=1.1,
                    )
                )
        return contracts

    def spot_quote(self, ticker: str) -> SpotQuote:
        return SpotQuote(
            ticker=ticker.upper(),
            spot_price=500.0,
            spot_observed_at=self.now,
            provider_received_at=self.now,
        )


def _option_symbol(ticker: str, expiration: date, option_type: OptionType, strike: float) -> str:
    flag = "C" if option_type == OptionType.CALL else "P"
    return f"{ticker}{expiration:%y%m%d}{flag}{int(strike * 1000):08d}"
