from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from math import erf, exp, log, pi, sqrt

from app.domain.calendar import DEFAULT_CALENDAR_VERSION


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


class PositionAssumption(str, Enum):
    CALL_POSITIVE_PUT_NEGATIVE = "call_positive_put_negative"


@dataclass(frozen=True)
class ContractSnapshot:
    option_symbol: str
    strike: float
    option_type: OptionType
    open_interest: int | None
    volume: int | None
    implied_volatility: float | None
    iv_observed_at: datetime | None
    oi_observed_date: date | None
    contract_multiplier: int | None
    underlying_close_at: datetime
    option_last_trade_at: datetime
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    mark: float | None = None
    vendor_delta: float | None = None
    vendor_gamma: float | None = None
    vendor_theta: float | None = None
    vendor_vega: float | None = None


@dataclass(frozen=True)
class AnalysisInput:
    ticker: str
    scope: str
    spot_price: float
    spot_observed_at: datetime | None
    spot_provider_received_at: datetime
    analyzed_at: datetime
    risk_free_rate: float
    dividend_yield: float
    position_assumption: PositionAssumption
    contracts: list[ContractSnapshot]
    pricing_model: str = "black_scholes_v1"
    model_version: str = "black_scholes_v1.0.0"
    calendar_version: str = str(DEFAULT_CALENDAR_VERSION)
    option_close_policy_version: str = "option_close_policy_v1"
    risk_free_rate_source: str = "configured"
    dividend_yield_source: str = "configured"


@dataclass(frozen=True)
class StrikeGexProxyRow:
    strike: float
    call_gex_proxy: float = 0.0
    put_gex_proxy: float = 0.0
    net_gex_proxy: float = 0.0
    call_oi: int = 0
    put_oi: int = 0
    call_volume: int = 0
    put_volume: int = 0


@dataclass(frozen=True)
class AnalysisModelInputs:
    pricing_model: str
    model_version: str
    calendar_version: str
    option_close_policy_version: str
    risk_free_rate: float
    risk_free_rate_source: str
    dividend_yield: float
    dividend_yield_source: str
    position_assumption: PositionAssumption
    spot_price: float
    spot_observed_at: datetime | None
    spot_provider_received_at: datetime
    analyzed_at: datetime


@dataclass(frozen=True)
class AnalysisResult:
    rows: dict[float, StrikeGexProxyRow]
    net_gex_proxy: float
    zero_gamma_proxy: float | None
    call_wall_proxy: float | None
    put_wall_proxy: float | None
    excluded_contract_count: int
    warnings: tuple[str, ...]
    model_inputs: AnalysisModelInputs


@dataclass(frozen=True)
class _PreparedContract:
    source: ContractSnapshot
    time_to_expiry_years: float
    gamma: float
    gex_proxy: float


def black_scholes_gamma(
    *,
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    implied_volatility: float,
    risk_free_rate: float,
    dividend_yield: float,
) -> float:
    if spot <= 0 or strike <= 0 or time_to_expiry_years <= 0 or implied_volatility <= 0:
        raise ValueError("spot, strike, time and IV must be positive")

    denominator = implied_volatility * sqrt(time_to_expiry_years)
    d1 = (
        log(spot / strike)
        + (risk_free_rate - dividend_yield + 0.5 * implied_volatility * implied_volatility) * time_to_expiry_years
    ) / denominator
    normal_pdf = exp(-0.5 * d1 * d1) / sqrt(2 * pi)
    return exp(-dividend_yield * time_to_expiry_years) * normal_pdf / (spot * denominator)


def analyze_gex_proxy(input_data: AnalysisInput) -> AnalysisResult:
    prepared, warnings, excluded = _prepare_contracts(input_data, input_data.spot_price)
    if input_data.contracts and not prepared:
        warnings.add("all_contracts_excluded")
    rows = _rows_for_prepared(prepared, input_data.spot_price)
    net = sum(row.net_gex_proxy for row in rows.values())
    zero_gamma = _zero_gamma(input_data) if prepared else None

    call_wall = None
    put_wall = None
    if rows:
        call_candidates = [row for row in rows.values() if row.call_gex_proxy > 0]
        put_candidates = [row for row in rows.values() if row.put_gex_proxy < 0]
        if call_candidates:
            call_wall = max(call_candidates, key=lambda row: row.call_gex_proxy).strike
        if put_candidates:
            put_wall = min(put_candidates, key=lambda row: row.put_gex_proxy).strike

    return AnalysisResult(
        rows=rows,
        net_gex_proxy=net,
        zero_gamma_proxy=zero_gamma,
        call_wall_proxy=call_wall,
        put_wall_proxy=put_wall,
        excluded_contract_count=excluded,
        warnings=tuple(sorted(warnings)),
        model_inputs=AnalysisModelInputs(
            pricing_model=input_data.pricing_model,
            model_version=input_data.model_version,
            calendar_version=input_data.calendar_version,
            option_close_policy_version=input_data.option_close_policy_version,
            risk_free_rate=input_data.risk_free_rate,
            risk_free_rate_source=input_data.risk_free_rate_source,
            dividend_yield=input_data.dividend_yield,
            dividend_yield_source=input_data.dividend_yield_source,
            position_assumption=input_data.position_assumption,
            spot_price=input_data.spot_price,
            spot_observed_at=input_data.spot_observed_at,
            spot_provider_received_at=input_data.spot_provider_received_at,
            analyzed_at=input_data.analyzed_at,
        ),
    )


def included_expiration_dates(input_data: AnalysisInput) -> tuple[date, ...]:
    return tuple(
        sorted(
            {
                contract.underlying_close_at.date()
                for contract in input_data.contracts
                if _exclusion_reason(input_data, contract) is None
            }
        )
    )


def _prepare_contracts(input_data: AnalysisInput, spot: float) -> tuple[list[_PreparedContract], set[str], int]:
    prepared: list[_PreparedContract] = []
    warnings: set[str] = set()
    excluded = 0

    for contract in input_data.contracts:
        reason = _exclusion_reason(input_data, contract)
        if reason is not None:
            warnings.add(reason)
            excluded += 1
            continue

        seconds = (contract.option_last_trade_at - input_data.analyzed_at).total_seconds()
        time_to_expiry_years = seconds / (365.0 * 24.0 * 60.0 * 60.0)
        if time_to_expiry_years <= 0:
            warnings.add("expired_contracts_excluded")
            excluded += 1
            continue

        gamma = black_scholes_gamma(
            spot=spot,
            strike=contract.strike,
            time_to_expiry_years=time_to_expiry_years,
            implied_volatility=contract.implied_volatility,
            risk_free_rate=input_data.risk_free_rate,
            dividend_yield=input_data.dividend_yield,
        )
        sign = 1.0 if contract.option_type == OptionType.CALL else -1.0
        gex_proxy = sign * gamma * contract.open_interest * contract.contract_multiplier * spot * spot * 0.01
        prepared.append(
            _PreparedContract(
                source=contract,
                time_to_expiry_years=time_to_expiry_years,
                gamma=gamma,
                gex_proxy=gex_proxy,
            )
        )

    return prepared, warnings, excluded


def _exclusion_reason(input_data: AnalysisInput, contract: ContractSnapshot) -> str | None:
    if input_data.analyzed_at >= contract.option_last_trade_at:
        return "expired_contracts_excluded"
    if contract.strike <= 0:
        return "non_positive_strike_excluded"
    if contract.implied_volatility is None:
        return "missing_iv_excluded"
    if contract.implied_volatility <= 0:
        return "non_positive_iv_excluded"
    if contract.open_interest is None:
        return "missing_open_interest_excluded"
    if contract.contract_multiplier is None:
        return "missing_multiplier_excluded"
    if contract.contract_multiplier <= 0:
        return "non_positive_multiplier_excluded"
    if (contract.option_last_trade_at - input_data.analyzed_at).total_seconds() <= 0:
        return "expired_contracts_excluded"
    return None


def _rows_for_prepared(prepared: list[_PreparedContract], spot: float) -> dict[float, StrikeGexProxyRow]:
    values: dict[float, dict[str, float | int]] = {}
    for item in prepared:
        row = values.setdefault(
            item.source.strike,
            {
                "call_gex_proxy": 0.0,
                "put_gex_proxy": 0.0,
                "call_oi": 0,
                "put_oi": 0,
                "call_volume": 0,
                "put_volume": 0,
            },
        )
        if item.source.option_type == OptionType.CALL:
            row["call_gex_proxy"] = float(row["call_gex_proxy"]) + item.gex_proxy
            row["call_oi"] = int(row["call_oi"]) + int(item.source.open_interest or 0)
            row["call_volume"] = int(row["call_volume"]) + int(item.source.volume or 0)
        else:
            row["put_gex_proxy"] = float(row["put_gex_proxy"]) + item.gex_proxy
            row["put_oi"] = int(row["put_oi"]) + int(item.source.open_interest or 0)
            row["put_volume"] = int(row["put_volume"]) + int(item.source.volume or 0)

    return {
        strike: StrikeGexProxyRow(
            strike=strike,
            call_gex_proxy=float(value["call_gex_proxy"]),
            put_gex_proxy=float(value["put_gex_proxy"]),
            net_gex_proxy=float(value["call_gex_proxy"]) + float(value["put_gex_proxy"]),
            call_oi=int(value["call_oi"]),
            put_oi=int(value["put_oi"]),
            call_volume=int(value["call_volume"]),
            put_volume=int(value["put_volume"]),
        )
        for strike, value in sorted(values.items())
    }


def _zero_gamma(input_data: AnalysisInput) -> float | None:
    if not input_data.contracts or input_data.spot_price <= 0:
        return None

    center = input_data.spot_price
    low = center * 0.75
    high = center * 1.25
    steps = 100
    points = [low + (high - low) * idx / steps for idx in range(steps + 1)]
    nets: list[tuple[float, float]] = []

    for spot in points:
        prepared, _, _ = _prepare_contracts(input_data, spot)
        rows = _rows_for_prepared(prepared, spot)
        nets.append((spot, sum(row.net_gex_proxy for row in rows.values())))

    crossings: list[float] = []
    for (left_spot, left_net), (right_spot, right_net) in zip(nets, nets[1:]):
        if left_net == 0:
            crossings.append(left_spot)
        if left_net == right_net:
            continue
        if (left_net < 0 < right_net) or (left_net > 0 > right_net):
            weight = abs(left_net) / (abs(left_net) + abs(right_net))
            crossings.append(left_spot + (right_spot - left_spot) * weight)

    if not crossings:
        return None
    return min(crossings, key=lambda level: abs(level - center))
