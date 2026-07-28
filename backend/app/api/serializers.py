from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.domain.gex import ContractSnapshot, StrikeGexProxyRow, included_expiration_dates
from app.services.capture import ChainCapture
from app.services.orchestrator import RuntimeAnalysisRun


def serialize_capture(capture: ChainCapture, *, include_contracts: bool = False) -> dict[str, Any]:
    return {
        "ticker": capture.ticker,
        "source": capture.source,
        "status": capture.status,
        "min_dte": capture.min_dte,
        "max_dte": capture.max_dte,
        "requested_expirations": [item.isoformat() for item in capture.requested_expirations],
        "chain_captured_at": _dt(capture.chain_captured_at),
        "provider_received_at": _dt(capture.provider_received_at),
        "expiration_captures": [
            serialize_expiration_capture(item, include_contracts=include_contracts)
            for item in capture.expiration_captures
        ],
        "warnings": list(capture.warnings),
    }


def serialize_expiration_capture(expiration, *, include_contracts: bool) -> dict[str, Any]:
    response = {
        "expiration_date": expiration.expiration_date.isoformat(),
        "underlying_close_at": _dt(expiration.underlying_close_at),
        "option_last_trade_at": _dt(expiration.option_last_trade_at),
        "status": expiration.status,
        "error_message": expiration.error_message,
        "contract_count": len(expiration.contracts),
    }
    if include_contracts:
        response["contracts"] = [serialize_contract(contract) for contract in expiration.contracts]
    return response


def serialize_contract(contract: ContractSnapshot) -> dict[str, Any]:
    return {
        "option_symbol": contract.option_symbol,
        "strike": contract.strike,
        "option_type": contract.option_type.value,
        "open_interest": contract.open_interest,
        "volume": contract.volume,
        "implied_volatility": contract.implied_volatility,
        "iv_observed_at": _dt(contract.iv_observed_at),
        "oi_observed_date": contract.oi_observed_date.isoformat() if contract.oi_observed_date else None,
        "contract_multiplier": contract.contract_multiplier,
        "underlying_close_at": _dt(contract.underlying_close_at),
        "option_last_trade_at": _dt(contract.option_last_trade_at),
        "bid": contract.bid,
        "ask": contract.ask,
        "last": contract.last,
        "mark": contract.mark,
        "vendor_delta": contract.vendor_delta,
        "vendor_gamma": contract.vendor_gamma,
        "vendor_theta": contract.vendor_theta,
        "vendor_vega": contract.vendor_vega,
    }


def serialize_run(run: RuntimeAnalysisRun, *, include_rows: bool) -> dict[str, Any]:
    result = run.result
    response = {
        "id": run.id,
        "chain_capture_id": run.chain_capture_id,
        "metric_name": "gex_proxy",
        "ticker": run.input_data.ticker,
        "scope": run.input_data.scope,
        "position_assumption": run.input_data.position_assumption.value,
        "pricing_model": result.model_inputs.pricing_model,
        "model_version": result.model_inputs.model_version,
        "calendar_version": result.model_inputs.calendar_version,
        "option_close_policy_version": result.model_inputs.option_close_policy_version,
        "risk_free_rate": result.model_inputs.risk_free_rate,
        "risk_free_rate_source": result.model_inputs.risk_free_rate_source,
        "dividend_yield": result.model_inputs.dividend_yield,
        "dividend_yield_source": result.model_inputs.dividend_yield_source,
        "spot_price": run.input_data.spot_price,
        "spot_observed_at": _dt(run.spot.spot_observed_at),
        "spot_provider_received_at": _dt(run.spot.provider_received_at),
        "chain_status": run.chain_capture.status,
        "chain_min_dte": run.chain_capture.min_dte,
        "chain_max_dte": run.chain_capture.max_dte,
        "requested_expirations": [item.isoformat() for item in run.chain_capture.requested_expirations],
        "included_expirations": [item.isoformat() for item in included_expiration_dates(run.input_data)],
        "chain_captured_at": _dt(run.chain_capture.chain_captured_at),
        "analyzed_at": _dt(run.input_data.analyzed_at),
        "net_gex_proxy": result.net_gex_proxy,
        "zero_gamma_proxy": result.zero_gamma_proxy,
        "call_wall_proxy": result.call_wall_proxy,
        "put_wall_proxy": result.put_wall_proxy,
        "excluded_contract_count": result.excluded_contract_count,
        "persist_strike_rows": run.persist_strike_rows,
        "iv_time_summary": _observed_summary("iv_observed_at", run),
        "oi_time_summary": _observed_summary("oi_observed_date", run),
        "warnings": list(result.warnings),
    }
    if include_rows:
        response["rows"] = [serialize_row(row) for row in result.rows.values()]
    return response


def serialize_row(row: StrikeGexProxyRow) -> dict[str, Any]:
    return {
        "strike": row.strike,
        "call_gex_proxy": row.call_gex_proxy,
        "put_gex_proxy": row.put_gex_proxy,
        "net_gex_proxy": row.net_gex_proxy,
        "call_oi": row.call_oi,
        "put_oi": row.put_oi,
        "call_volume": row.call_volume,
        "put_volume": row.put_volume,
    }


def _observed_summary(field: str, run: RuntimeAnalysisRun) -> dict[str, str | None | int]:
    values = []
    for contract in run.input_data.contracts:
        value = getattr(contract, field)
        if value is not None:
            values.append(value)
    if not values:
        return {"min": None, "max": None, "known_count": 0, "unknown_count": len(run.input_data.contracts)}
    return {
        "min": _value(values[0] if len(values) == 1 else min(values)),
        "max": _value(values[0] if len(values) == 1 else max(values)),
        "known_count": len(values),
        "unknown_count": len(run.input_data.contracts) - len(values),
    }


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _value(value: datetime | date) -> str:
    return value.isoformat()
