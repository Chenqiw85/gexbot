from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime
from typing import Sequence

from app.config import get_settings
from app.domain.ticker import normalize_ticker
from app.providers.base import MarketDataProvider, ProviderDataError, ProviderOptionContract, SpotQuote
from app.services.orchestrator import make_provider


@dataclass(frozen=True)
class ProviderSmokeResult:
    ticker: str
    source: str
    spot_price: float
    spot_observed_at: datetime | None
    spot_provider_received_at: datetime
    expirations: tuple[date, ...]
    expiration_count: int
    contract_count: int
    has_iv: bool
    has_open_interest: bool
    has_volume: bool
    has_contract_multiplier: bool
    warnings: tuple[str, ...]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "source": self.source,
            "spot_price": self.spot_price,
            "spot_observed_at": _dt(self.spot_observed_at),
            "spot_provider_received_at": _dt(self.spot_provider_received_at),
            "expirations": [item.isoformat() for item in self.expirations],
            "expiration_count": self.expiration_count,
            "contract_count": self.contract_count,
            "has_iv": self.has_iv,
            "has_open_interest": self.has_open_interest,
            "has_volume": self.has_volume,
            "has_contract_multiplier": self.has_contract_multiplier,
            "warnings": list(self.warnings),
        }


def run_provider_smoke(
    provider: MarketDataProvider,
    *,
    ticker: str,
    max_expirations: int = 1,
) -> ProviderSmokeResult:
    if max_expirations <= 0:
        raise ValueError("max_expirations must be positive")

    normalized = normalize_ticker(ticker)
    expirations = provider.available_expirations(normalized)
    if not expirations:
        raise ProviderDataError(f"{provider.source} returned no expirations for {normalized}")

    selected = tuple(expirations[:max_expirations])
    contracts: list[ProviderOptionContract] = []
    warnings: list[str] = []
    for expiration in selected:
        try:
            contracts.extend(provider.option_chain_for_expiration(normalized, expiration))
        except Exception as exc:
            warnings.append(f"chain_failed:{expiration.isoformat()}:{exc}")

    if not contracts:
        raise ProviderDataError(f"{provider.source} returned no option contracts for {normalized}")

    spot = provider.spot_quote(normalized)
    if spot.spot_price <= 0:
        raise ProviderDataError(f"{provider.source} returned non-positive spot for {normalized}")

    warnings.extend(_field_warnings(contracts))
    return ProviderSmokeResult(
        ticker=normalized,
        source=provider.source,
        spot_price=spot.spot_price,
        spot_observed_at=spot.spot_observed_at,
        spot_provider_received_at=spot.provider_received_at,
        expirations=selected,
        expiration_count=len(selected),
        contract_count=len(contracts),
        has_iv=any(contract.implied_volatility is not None for contract in contracts),
        has_open_interest=any(contract.open_interest is not None for contract in contracts),
        has_volume=any(contract.volume is not None for contract in contracts),
        has_contract_multiplier=any(contract.contract_multiplier is not None for contract in contracts),
        warnings=tuple(warnings),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the configured market data provider.")
    parser.add_argument("--ticker", default="SPY")
    parser.add_argument("--max-expirations", type=int, default=1)
    args = parser.parse_args(argv)

    try:
        provider = make_provider(get_settings())
        result = run_provider_smoke(provider, ticker=args.ticker, max_expirations=args.max_expirations)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2

    missing_required = _missing_required_gex_inputs(result)
    if missing_required:
        print(
            json.dumps(
                {
                    "ok": False,
                    **result.to_json_dict(),
                    "missing_required_inputs": missing_required,
                    "error": f"missing required GEX inputs: {', '.join(missing_required)}",
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    print(json.dumps({"ok": True, **result.to_json_dict()}, indent=2, sort_keys=True))
    return 0


def _field_warnings(contracts: list[ProviderOptionContract]) -> list[str]:
    warnings = []
    if not any(contract.implied_volatility is not None for contract in contracts):
        warnings.append("missing_iv")
    if not any(contract.open_interest is not None for contract in contracts):
        warnings.append("missing_open_interest")
    if not any(contract.volume is not None for contract in contracts):
        warnings.append("missing_volume")
    if not any(contract.contract_multiplier is not None for contract in contracts):
        warnings.append("missing_contract_multiplier")
    return warnings


def _missing_required_gex_inputs(result: ProviderSmokeResult) -> list[str]:
    missing = []
    if not result.has_iv:
        missing.append("iv")
    if not result.has_open_interest:
        missing.append("open_interest")
    if not result.has_contract_multiplier:
        missing.append("contract_multiplier")
    return missing


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


if __name__ == "__main__":
    raise SystemExit(main())
