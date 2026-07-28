from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.domain.calendar import NY, MarketCalendar, build_expiration_times, policy_for_ticker
from app.domain.gex import ContractSnapshot
from app.domain.ticker import normalize_ticker
from app.providers.base import MarketDataProvider


@dataclass(frozen=True)
class ChainCaptureRequest:
    ticker: str
    min_dte: int = 0
    max_dte: int = 45
    expirations: list[date] | None = None
    max_expirations: int = 12


@dataclass(frozen=True)
class ExpirationCapture:
    expiration_date: date
    underlying_close_at: datetime
    option_last_trade_at: datetime
    status: str
    provider_observed_at: datetime | None
    error_message: str | None
    contracts: list[ContractSnapshot]


@dataclass(frozen=True)
class ChainCapture:
    ticker: str
    source: str
    status: str
    min_dte: int
    max_dte: int
    requested_expirations: list[date]
    chain_captured_at: datetime
    provider_received_at: datetime
    expiration_captures: list[ExpirationCapture]
    warnings: tuple[str, ...]


def capture_option_chain(
    provider: MarketDataProvider,
    request: ChainCaptureRequest,
    *,
    market_calendar: MarketCalendar | None = None,
) -> ChainCapture:
    if request.min_dte > request.max_dte:
        raise ValueError("min_dte must be less than or equal to max_dte")
    ticker = normalize_ticker(request.ticker)
    calendar = market_calendar or MarketCalendar()
    available = provider.available_expirations(ticker)
    provider_received_at = getattr(provider, "now", datetime.now().astimezone())
    selected = _select_expirations(available, request, anchor_date=provider_received_at.astimezone(NY).date())
    policy = policy_for_ticker(ticker)

    if not selected:
        return ChainCapture(
            ticker=ticker,
            source=provider.source,
            status="failed",
            min_dte=request.min_dte,
            max_dte=request.max_dte,
            requested_expirations=[],
            chain_captured_at=provider_received_at,
            provider_received_at=provider_received_at,
            expiration_captures=[],
            warnings=("no_expirations_selected",),
        )

    expiration_captures: list[ExpirationCapture] = []
    for expiration in selected:
        times = build_expiration_times(ticker, expiration, policy, market_calendar=calendar)
        try:
            provider_contracts = provider.option_chain_for_expiration(ticker, expiration)
            if not provider_contracts:
                raise ValueError(f"no option contracts returned for {ticker} {expiration.isoformat()}")
        except Exception as exc:  # Provider boundary records partial failure.
            expiration_captures.append(
                ExpirationCapture(
                    expiration_date=expiration,
                    underlying_close_at=times.underlying_close_at,
                    option_last_trade_at=times.option_last_trade_at,
                    status="failed",
                    provider_observed_at=None,
                    error_message=str(exc),
                    contracts=[],
                )
            )
            continue

        expiration_captures.append(
            ExpirationCapture(
                expiration_date=expiration,
                underlying_close_at=times.underlying_close_at,
                option_last_trade_at=times.option_last_trade_at,
                status="success",
                provider_observed_at=None,
                error_message=None,
                contracts=[
                    ContractSnapshot(
                        option_symbol=contract.option_symbol,
                        strike=contract.strike,
                        option_type=contract.option_type,
                        open_interest=contract.open_interest,
                        volume=contract.volume,
                        implied_volatility=contract.implied_volatility,
                        iv_observed_at=contract.iv_observed_at,
                        oi_observed_date=contract.oi_observed_date,
                        contract_multiplier=contract.contract_multiplier,
                        underlying_close_at=times.underlying_close_at,
                        option_last_trade_at=times.option_last_trade_at,
                        bid=contract.bid,
                        ask=contract.ask,
                        last=contract.last,
                        mark=contract.mark,
                        vendor_delta=contract.vendor_delta,
                        vendor_gamma=contract.vendor_gamma,
                        vendor_theta=contract.vendor_theta,
                        vendor_vega=contract.vendor_vega,
                    )
                    for contract in provider_contracts
                ],
            )
        )

    failed = [item for item in expiration_captures if item.status == "failed"]
    succeeded = [item for item in expiration_captures if item.status == "success"]
    if failed and succeeded:
        status = "partial"
        warnings = ("partial_capture",)
    elif failed:
        status = "failed"
        warnings = ("capture_failed",)
    else:
        status = "success"
        warnings = ()

    return ChainCapture(
        ticker=ticker,
        source=provider.source,
        status=status,
        min_dte=request.min_dte,
        max_dte=request.max_dte,
        requested_expirations=selected,
        chain_captured_at=provider_received_at,
        provider_received_at=provider_received_at,
        expiration_captures=expiration_captures,
        warnings=warnings,
    )


def _select_expirations(available: list[date], request: ChainCaptureRequest, *, anchor_date: date) -> list[date]:
    explicit = set(request.expirations) if request.expirations is not None else None
    selected = [
        expiration
        for expiration in available
        if request.min_dte <= (expiration - anchor_date).days <= request.max_dte
        and (explicit is None or expiration in explicit)
    ]
    return selected[: request.max_expirations]
