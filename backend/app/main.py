from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import get_settings
from app.domain.calendar import NY, MarketCalendar, build_expiration_times, policy_for_ticker
from app.domain.ticker import normalize_ticker
from app.api.serializers import serialize_capture, serialize_run
from app.providers.base import ProviderError
from app.providers.http import HttpRequestError, RateLimitExceeded
from app.services.capture import ChainCaptureRequest
from app.services.orchestrator import RuntimeState, runtime_run_to_stored
from app.services.history import UnsupportedModelVersion, recompute_from_record


def create_app(runtime_state: RuntimeState | None = None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="GEX Proxy Bot API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.runtime_state = runtime_state or RuntimeState(settings=settings)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/api/v1/tickers")
    def list_tickers():
        state = _state(app)
        return {"tickers": [ticker.__dict__ for ticker in state.list_tickers()]}

    @app.post("/api/v1/tickers")
    def add_ticker(body: TickerCreate):
        state = _state(app)
        return state.add_ticker(body.ticker, body.dividend_yield).__dict__

    @app.patch("/api/v1/tickers/{ticker}")
    def update_ticker(ticker: str, body: TickerUpdate):
        state = _state(app)
        normalized = _normalize_query_ticker(ticker)
        return state.update_ticker(
            normalized,
            enabled=body.enabled,
            dividend_yield=body.dividend_yield,
        ).__dict__

    @app.post("/api/v1/chains/capture")
    def capture_chain(body: ChainCaptureBody, include_contracts: bool = False):
        state = _state(app)
        settings = state.settings
        min_dte = settings.default_min_dte if body.min_dte is None else body.min_dte
        max_dte = settings.default_max_dte if body.max_dte is None else body.max_dte
        if min_dte > max_dte:
            raise HTTPException(status_code=422, detail="min_dte must be less than or equal to max_dte")
        capture = state.capture_chain(
            ChainCaptureRequest(
                ticker=body.ticker,
                min_dte=min_dte,
                max_dte=max_dte,
                expirations=body.expirations,
                max_expirations=settings.default_max_expirations,
            )
        )
        if capture.status == "failed":
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "code": "capture_failed",
                        "message": "all requested option expirations failed",
                        "warnings": list(capture.warnings),
                    }
                },
            )
        return serialize_capture(capture, include_contracts=include_contracts)

    @app.get("/api/v1/chains/latest")
    def latest_chain_capture(ticker: str = Query(min_length=1), include_contracts: bool = False):
        state = _state(app)
        ticker = _normalize_query_ticker(ticker)
        capture = state.latest_chain_capture(ticker=ticker)
        if capture is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": "no_latest_chain_capture",
                        "message": f"no latest option chain capture found for {ticker.upper()}",
                    }
                },
            )
        return serialize_capture(capture, include_contracts=include_contracts)

    @app.post("/api/v1/gex-proxy/analyze")
    def analyze(body: AnalyzeBody):
        state = _state(app)
        run = state.analyze_latest(
            ticker=body.ticker,
            scope=body.scope,
            persist_strike_rows=body.persist_strike_rows,
            allow_chain_capture=body.allow_chain_capture,
        )
        return serialize_run(run, include_rows=body.persist_strike_rows)

    @app.get("/api/v1/gex-proxy/latest")
    def latest(
        ticker: str = Query(min_length=1),
        scope: Literal["all", "0dte"] = "all",
        include_rows: bool = True,
    ):
        state = _state(app)
        ticker = _normalize_query_ticker(ticker)
        run = state.latest_run(ticker=ticker, scope=scope)
        if run is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": "no_latest_run",
                        "message": f"no latest GEX proxy run found for {ticker.upper()} {scope}",
                    }
                },
            )
        return serialize_run(run, include_rows=include_rows)

    @app.get("/api/v1/gex-proxy/history")
    def history(
        ticker: str = Query(min_length=1),
        scope: Literal["all", "0dte"] = "all",
        date: date | None = None,
        limit: int = Query(default=2000, ge=1, le=5000),
    ):
        state = _state(app)
        ticker = _normalize_query_ticker(ticker)
        runs = state.history_runs(ticker=ticker, scope=scope, date_filter=date, limit=limit)
        return {"runs": [serialize_run(run, include_rows=False) for run in runs]}

    @app.get("/api/v1/gex-proxy/runs/{run_id}")
    def get_run(run_id: int, include_rows: bool = True):
        state = _state(app)
        run = state.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="analysis run not found")
        return serialize_run(run, include_rows=include_rows)

    @app.post("/api/v1/gex-proxy/runs/{run_id}/persist-rows")
    def persist_rows(run_id: int):
        state = _state(app)
        run = state.persist_rows(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="analysis run not found")
        return serialize_run(run, include_rows=True)

    @app.post("/api/v1/gex-proxy/runs/{run_id}/recompute")
    def recompute(run_id: int):
        state = _state(app)
        run = state.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="analysis run not found")
        try:
            recomputed = recompute_from_record(runtime_run_to_stored(run))
        except UnsupportedModelVersion as exc:
            return JSONResponse(
                status_code=409,
                content={"error": {"code": "unsupported_model_version", "message": str(exc)}},
            )
        return {
            "run_id": run_id,
            "original_net_gex_proxy": run.result.net_gex_proxy,
            "recomputed_net_gex_proxy": recomputed.net_gex_proxy,
            "diff": recomputed.net_gex_proxy - run.result.net_gex_proxy,
            "net_gex_proxy": _diff_field(run.result.net_gex_proxy, recomputed.net_gex_proxy),
            "zero_gamma_proxy": _diff_field(run.result.zero_gamma_proxy, recomputed.zero_gamma_proxy),
            "call_wall_proxy": _diff_field(run.result.call_wall_proxy, recomputed.call_wall_proxy),
            "put_wall_proxy": _diff_field(run.result.put_wall_proxy, recomputed.put_wall_proxy),
            "warnings": list(recomputed.warnings),
            "model_version": recomputed.model_inputs.model_version,
            "calendar_version": recomputed.model_inputs.calendar_version,
            "option_close_policy_version": recomputed.model_inputs.option_close_policy_version,
        }

    @app.get("/api/v1/market/status")
    def market_status(ticker: str = Query(default="SPY")):
        calendar = MarketCalendar()
        settings = _state(app).settings
        normalized = _normalize_query_ticker(ticker)
        now = datetime.now(timezone.utc)
        local_now = now.astimezone(NY)
        session = calendar.session_for(local_now.date()) if calendar.is_trading_day(local_now.date()) else None
        policy = policy_for_ticker(normalized)
        expiration_times = (
            build_expiration_times(normalized, local_now.date(), policy, market_calendar=calendar)
            if session is not None
            else None
        )
        return {
            "ticker": normalized,
            "calendar_version": str(calendar.version),
            "market_date": local_now.date().isoformat(),
            "is_trading_day": session is not None,
            "is_half_day": session.is_half_day if session else False,
            "is_underlying_market_open": (
                session is not None and session.open_at <= local_now <= session.underlying_close_at
            ),
            "is_option_market_open": (
                session is not None
                and expiration_times is not None
                and session.open_at <= local_now <= expiration_times.option_last_trade_at
            ),
            "underlying_open_at": session.open_at.isoformat() if session else None,
            "underlying_close_at": session.underlying_close_at.isoformat() if session else None,
            "option_last_trade_at": expiration_times.option_last_trade_at.isoformat() if expiration_times else None,
            "option_last_trade_policy": policy.name,
            "chain_refresh_seconds": settings.chain_refresh_seconds,
            "spot_refresh_seconds": settings.spot_refresh_seconds,
        }

    @app.exception_handler(RateLimitExceeded)
    def rate_limit_handler(_, exc: RateLimitExceeded):
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "provider_rate_limited",
                    "message": exc.message,
                    "rate_limit_available": exc.rate_limit_available,
                    "rate_limit_expiry": exc.rate_limit_expiry,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    def validation_error_handler(_, exc: RequestValidationError):
        fields = [_validation_error_field(error) for error in exc.errors()]
        message = "; ".join(f"{field['path']}: {field['message']}" for field in fields)
        return _error_response(
            422,
            code="validation_error",
            message=message or "request validation failed",
            fields=fields,
        )

    @app.exception_handler(HTTPException)
    def http_exception_handler(_, exc: HTTPException):
        if exc.status_code == 422:
            return _error_response(422, code="validation_error", message=str(exc.detail))
        if exc.status_code == 404:
            return _error_response(404, code="not_found", message=str(exc.detail))
        return _error_response(exc.status_code, code="http_error", message=str(exc.detail))

    @app.exception_handler(ProviderError)
    def provider_error_handler(_, exc: Exception):
        return JSONResponse(
            status_code=502,
            content={"error": {"code": "provider_error", "message": str(exc)}},
        )

    @app.exception_handler(HttpRequestError)
    def http_request_error_handler(_, exc: Exception):
        status_code = exc.status_code if isinstance(exc, HttpRequestError) else None
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "provider_error",
                    "message": str(exc),
                    "provider_status_code": status_code,
                }
            },
        )

    return app


def _state(app: FastAPI) -> RuntimeState:
    return app.state.runtime_state


def _normalize_query_ticker(value: str) -> str:
    try:
        return normalize_ticker(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _diff_field(original: float | None, recomputed: float | None) -> dict[str, float | None]:
    return {
        "original": original,
        "recomputed": recomputed,
        "diff": None if original is None or recomputed is None else recomputed - original,
    }


def _error_response(status_code: int, *, code: str, message: str, **extra) -> JSONResponse:
    error = {"code": code, "message": message}
    error.update({key: value for key, value in extra.items() if value is not None})
    return JSONResponse(status_code=status_code, content={"error": error})


def _validation_error_field(error: dict) -> dict[str, str]:
    loc = error.get("loc", ())
    path = ".".join(str(part) for part in loc) if isinstance(loc, (list, tuple)) else str(loc)
    return {
        "path": path,
        "message": str(error.get("msg", "invalid value")),
    }


class TickerCreate(BaseModel):
    ticker: str = Field(min_length=1)
    dividend_yield: float | None = Field(default=None, ge=0, le=1)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker_value(cls, value: str) -> str:
        return normalize_ticker(value)


class TickerUpdate(BaseModel):
    enabled: bool | None = None
    dividend_yield: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def require_update_field(self):
        if self.enabled is None and self.dividend_yield is None:
            raise ValueError("enabled or dividend_yield is required")
        return self


class ChainCaptureBody(BaseModel):
    ticker: str = Field(min_length=1)
    min_dte: int | None = Field(default=None, ge=0)
    max_dte: int | None = Field(default=None, ge=0)
    expirations: list[date] | None = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker_value(cls, value: str) -> str:
        return normalize_ticker(value)

    @model_validator(mode="after")
    def validate_dte_range(self):
        if self.min_dte is not None and self.max_dte is not None and self.min_dte > self.max_dte:
            raise ValueError("min_dte must be less than or equal to max_dte")
        return self


class AnalyzeBody(BaseModel):
    ticker: str = Field(min_length=1)
    scope: Literal["all", "0dte"] = "all"
    persist_strike_rows: bool = False
    allow_chain_capture: bool = False

    @field_validator("ticker")
    @classmethod
    def normalize_ticker_value(cls, value: str) -> str:
        return normalize_ticker(value)


app = create_app()
