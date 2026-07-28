from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from itertools import count
from typing import Callable

from app.config import Settings, get_settings
from app.db.repository import PersistedAnalysisRun, PostgresRepository
from app.domain.calendar import NY
from app.domain.gex import AnalysisInput, AnalysisResult, PositionAssumption, analyze_gex_proxy
from app.domain.ticker import AppTicker, normalize_ticker
from app.providers.base import MarketDataProvider, ProviderDataError, ProviderError, SpotQuote
from app.providers.mock import MockMarketDataProvider
from app.providers.tradier import TradierProvider
from app.services.capture import ChainCapture, ChainCaptureRequest, capture_option_chain
from app.services.persistence import StoredAnalysisRun


class TickerNotEnabledError(ProviderError):
    """Raised when a capture/analysis targets a ticker that is not registered or enabled.

    This is enforced before any provider (Tradier) access so that unknown or
    disabled tickers never trigger an outbound market-data request.
    """


@dataclass(frozen=True)
class RuntimeAnalysisRun:
    id: int
    chain_capture_id: int
    chain_capture: ChainCapture
    spot: SpotQuote
    input_data: AnalysisInput
    result: AnalysisResult
    persist_strike_rows: bool


@dataclass(frozen=True)
class RuntimeSpotObservation:
    spot: SpotQuote
    spot_observation_id: int | None


class RuntimeState:
    def __init__(
        self,
        settings: Settings | None = None,
        provider: MarketDataProvider | None = None,
        repository: PostgresRepository | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.provider = provider or make_provider(self.settings)
        # Guards the in-memory mutable state (ticker registry, chain-capture and
        # analysis-run dictionaries, and the id generators) so that synchronous
        # FastAPI handlers running on different worker threads cannot corrupt it.
        # The lock is never held across outbound HTTP requests or GEX math.
        self._lock = threading.RLock()
        self.repository = repository
        if self.repository is None and self.settings.database_url:
            self.repository = PostgresRepository.connect(
                self.settings.database_url,
                min_size=self.settings.db_pool_min_size,
                max_size=self.settings.db_pool_max_size,
                timeout=self.settings.db_pool_timeout_seconds,
            )
        if self.repository and self.settings.auto_ensure_schema:
            self.repository.ensure_schema()
        self.tickers = {}
        for ticker in self.settings.default_tickers:
            self._ensure_default_ticker(ticker)
        self.chain_captures: dict[str, list[ChainCapture]] = {}
        self.chain_capture_ids: dict[int, int] = {}
        self._chain_ids = count(1)
        self.analysis_runs: dict[int, RuntimeAnalysisRun] = {}
        self.analysis_run_ids_by_idempotency_key: dict[str, int] = {}
        self._analysis_ids = count(1)

    def close(self) -> None:
        """Release database resources (connection pool) held by this state."""
        repository = self.repository
        if repository is not None and hasattr(repository, "close"):
            repository.close()

    def list_tickers(self) -> list[AppTicker]:
        if self.repository:
            return self.repository.list_underlyings()
        with self._lock:
            return sorted(
                (ticker for ticker in self.tickers.values() if ticker.enabled),
                key=lambda item: item.ticker,
            )

    def is_ticker_enabled(self, ticker: str) -> bool:
        normalized = normalize_ticker(ticker)
        return normalized in set(self.ticker_symbols())

    def _require_enabled_ticker(self, normalized: str) -> None:
        if not self.is_ticker_enabled(normalized):
            raise TickerNotEnabledError(
                f"ticker {normalized} is not registered or is disabled"
            )

    def add_ticker(self, ticker: str, dividend_yield: float | None = None) -> AppTicker:
        normalized = normalize_ticker(ticker)
        resolved_dividend_yield = self.settings.default_dividend_yield if dividend_yield is None else dividend_yield
        if self.repository:
            return self.repository.upsert_underlying(normalized, resolved_dividend_yield)
        record = AppTicker(
            ticker=normalized,
            dividend_yield=resolved_dividend_yield,
        )
        with self._lock:
            self.tickers[normalized] = record
        return record

    def update_ticker(
        self,
        ticker: str,
        *,
        enabled: bool | None = None,
        dividend_yield: float | None = None,
    ) -> AppTicker:
        normalized = normalize_ticker(ticker)
        if self.repository:
            return self.repository.update_underlying(
                normalized,
                enabled=enabled,
                dividend_yield=dividend_yield,
                default_dividend_yield=self.settings.default_dividend_yield,
            )
        with self._lock:
            existing = self.tickers.get(normalized)
            resolved_dividend_yield = existing.dividend_yield if existing else self.settings.default_dividend_yield
            resolved_enabled = existing.enabled if existing else True
            if dividend_yield is not None:
                resolved_dividend_yield = dividend_yield
            if enabled is not None:
                resolved_enabled = enabled
            record = AppTicker(
                ticker=normalized,
                dividend_yield=resolved_dividend_yield,
                enabled=resolved_enabled,
            )
            self.tickers[normalized] = record
        return record

    def ticker_symbols(self) -> list[str]:
        return [ticker.ticker for ticker in self.list_tickers() if ticker.enabled]

    def capture_chain(self, request: ChainCaptureRequest) -> ChainCapture:
        normalized = normalize_ticker(request.ticker)
        # Reject unknown/disabled tickers before any outbound provider request.
        self._require_enabled_ticker(normalized)
        # Outbound provider I/O and DB persistence happen without holding the lock.
        capture = capture_option_chain(self.provider, request)
        if self.repository:
            capture_id = self.repository.save_chain_capture(capture)
        else:
            with self._lock:
                capture_id = next(self._chain_ids)
        with self._lock:
            self.chain_captures.setdefault(capture.ticker, []).append(capture)
            self.chain_capture_ids[id(capture)] = capture_id
        return capture

    def latest_chain_capture(self, *, ticker: str) -> ChainCapture | None:
        _, capture = self._latest_capture_record(normalize_ticker(ticker))
        return capture

    def observe_spot(self, ticker: str) -> RuntimeSpotObservation:
        normalized = normalize_ticker(ticker)
        spot = self.provider.spot_quote(normalized)
        spot_id = self.repository.save_spot_observation(spot, self.provider.source) if self.repository else None
        return RuntimeSpotObservation(spot=spot, spot_observation_id=spot_id)

    def analyze_latest(
        self,
        *,
        ticker: str,
        scope: str,
        persist_strike_rows: bool,
        allow_chain_capture: bool = False,
        idempotency_key: str | None = None,
        spot_observation: RuntimeSpotObservation | None = None,
        spot_observation_factory: Callable[[], RuntimeSpotObservation] | None = None,
    ) -> RuntimeAnalysisRun:
        if idempotency_key is not None:
            if self.repository:
                persisted = self.repository.analysis_run_by_idempotency_key(idempotency_key)
                if persisted is not None:
                    run = _runtime_from_persisted(persisted)
                    with self._lock:
                        self.analysis_runs[run.id] = run
                    return run
            else:
                with self._lock:
                    existing_id = self.analysis_run_ids_by_idempotency_key.get(idempotency_key)
                    if existing_id is not None:
                        return self.analysis_runs[existing_id]

        normalized = normalize_ticker(ticker)
        # Reject unknown/disabled tickers before any outbound provider request.
        self._require_enabled_ticker(normalized)
        capture_id, capture = self._latest_capture_record(normalized)
        if capture is None:
            if not allow_chain_capture:
                raise ProviderDataError(f"no usable option chain capture for {normalized}")
            capture = self.capture_chain(
                ChainCaptureRequest(
                    ticker=normalized,
                    min_dte=self.settings.default_min_dte,
                    max_dte=self.settings.default_max_dte,
                    max_expirations=self.settings.default_max_expirations,
                )
            )
            capture_id = self.chain_capture_ids.get(id(capture))
            if capture.status == "failed":
                raise ProviderDataError(f"initial option chain capture failed for {normalized}")
        if capture_id is None:
            raise ProviderDataError(f"no chain capture id for {normalized}")
        if spot_observation is None:
            spot_observation = (
                spot_observation_factory()
                if spot_observation_factory is not None
                else self.observe_spot(normalized)
            )
        spot = spot_observation.spot
        analyzed_at = datetime.now(timezone.utc)
        spot_id = spot_observation.spot_observation_id
        if self.repository and spot_id is None:
            spot_id = self.repository.save_spot_observation(spot, self.provider.source)
        ticker_record = self._ticker_for_analysis(normalized)
        contracts = [
            contract
            for expiration_capture in capture.expiration_captures
            if expiration_capture.status == "success"
            for contract in expiration_capture.contracts
            if scope != "0dte" or contract.underlying_close_at.astimezone(NY).date() == analyzed_at.astimezone(NY).date()
        ]
        warnings = list(capture.warnings)
        if capture.status == "partial":
            warnings.append("analysis_uses_partial_capture")

        input_data = AnalysisInput(
            ticker=normalized,
            scope=scope,
            spot_price=spot.spot_price,
            spot_observed_at=spot.spot_observed_at,
            spot_provider_received_at=spot.provider_received_at,
            analyzed_at=analyzed_at,
            risk_free_rate=self.settings.default_risk_free_rate,
            dividend_yield=ticker_record.dividend_yield,
            position_assumption=PositionAssumption.CALL_POSITIVE_PUT_NEGATIVE,
            contracts=contracts,
        )
        result = analyze_gex_proxy(input_data)
        if warnings:
            result = AnalysisResult(
                rows=result.rows,
                net_gex_proxy=result.net_gex_proxy,
                zero_gamma_proxy=result.zero_gamma_proxy,
                call_wall_proxy=result.call_wall_proxy,
                put_wall_proxy=result.put_wall_proxy,
                excluded_contract_count=result.excluded_contract_count,
                warnings=tuple(sorted(set(result.warnings + tuple(warnings)))),
                model_inputs=result.model_inputs,
            )
        if self.repository and spot_id is not None:
            # The database enforces idempotency atomically via ON CONFLICT.
            run_id = self.repository.save_analysis_run(
                chain_capture_id=capture_id,
                spot_observation_id=spot_id,
                input_data=input_data,
                result=result,
                persist_strike_rows=persist_strike_rows,
                warnings=result.warnings,
                min_dte=capture.min_dte,
                max_dte=capture.max_dte,
                idempotency_key=idempotency_key,
            )
            run = RuntimeAnalysisRun(
                id=run_id,
                chain_capture_id=capture_id,
                chain_capture=capture,
                spot=spot,
                input_data=input_data,
                result=result,
                persist_strike_rows=persist_strike_rows,
            )
            with self._lock:
                self.analysis_runs[run_id] = run
            return run

        # In-memory store. Re-check idempotency under the lock so two concurrent
        # same-key requests converge on a single logical run with one id, and the
        # id generator and run dictionaries are mutated atomically.
        with self._lock:
            if idempotency_key is not None:
                existing_id = self.analysis_run_ids_by_idempotency_key.get(idempotency_key)
                if existing_id is not None:
                    return self.analysis_runs[existing_id]
            run_id = next(self._analysis_ids)
            run = RuntimeAnalysisRun(
                id=run_id,
                chain_capture_id=capture_id,
                chain_capture=capture,
                spot=spot,
                input_data=input_data,
                result=result,
                persist_strike_rows=persist_strike_rows,
            )
            self.analysis_runs[run_id] = run
            if idempotency_key is not None:
                self.analysis_run_ids_by_idempotency_key[idempotency_key] = run_id
            return run

    def latest_run(self, *, ticker: str, scope: str) -> RuntimeAnalysisRun | None:
        normalized = normalize_ticker(ticker)
        if self.repository:
            persisted = self.repository.latest_analysis_run(normalized, scope)
            return _runtime_from_persisted(persisted) if persisted else None
        candidates = [
            run
            for run in self.analysis_runs.values()
            if run.input_data.ticker == normalized and run.input_data.scope == scope
        ]
        return max(candidates, key=lambda run: run.id) if candidates else None

    def history_runs(
        self,
        *,
        ticker: str,
        scope: str,
        date_filter: date | None = None,
        limit: int = 2000,
    ) -> list[RuntimeAnalysisRun]:
        normalized = normalize_ticker(ticker)
        if self.repository:
            return [
                _runtime_from_persisted(run)
                for run in self.repository.history_runs(normalized, scope, date_filter=date_filter, limit=limit)
            ]
        else:
            runs = [
                run
                for run in sorted(self.analysis_runs.values(), key=lambda item: item.id)
                if run.input_data.ticker == normalized and run.input_data.scope == scope
            ]
        if date_filter is None:
            return runs[:limit]
        return [run for run in runs if run.input_data.analyzed_at.astimezone(NY).date() == date_filter][:limit]

    def get_run(self, run_id: int) -> RuntimeAnalysisRun | None:
        if self.repository:
            try:
                return _runtime_from_persisted(self.repository.load_analysis_run(run_id))
            except KeyError:
                return None
        return self.analysis_runs.get(run_id)

    def persist_rows(self, run_id: int) -> RuntimeAnalysisRun | None:
        if self.repository:
            try:
                return _runtime_from_persisted(self.repository.persist_analysis_run_rows(run_id))
            except KeyError:
                return None
        with self._lock:
            run = self.analysis_runs.get(run_id)
            if run is None:
                return None
            checkpoint = replace(run, persist_strike_rows=True)
            self.analysis_runs[run_id] = checkpoint
            return checkpoint

    def _latest_capture_record(self, ticker: str) -> tuple[int | None, ChainCapture | None]:
        ticker = normalize_ticker(ticker)
        if self.repository:
            persisted = self.repository.latest_chain_capture(ticker)
            if persisted:
                return persisted.id, persisted.capture
        with self._lock:
            captures = list(self.chain_captures.get(ticker, []))
            usable = [item for item in captures if item.status in {"success", "partial"}]
            capture = usable[-1] if usable else None
            return (self.chain_capture_ids.get(id(capture)) if capture else None), capture

    def _ensure_default_ticker(self, ticker: str) -> None:
        normalized = normalize_ticker(ticker)
        if self.repository:
            self.repository.ensure_underlying(normalized, self.settings.default_dividend_yield)
            return
        with self._lock:
            self.tickers[normalized] = AppTicker(
                ticker=normalized,
                dividend_yield=self.settings.default_dividend_yield,
            )

    def _ticker_for_analysis(self, ticker: str) -> AppTicker:
        normalized = normalize_ticker(ticker)
        for record in self.list_tickers():
            if record.ticker == normalized:
                return record
        return self.add_ticker(normalized)


def make_provider(settings: Settings) -> MarketDataProvider:
    if settings.provider == "tradier":
        if not settings.tradier_token:
            raise RuntimeError("TRADIER_TOKEN is required when GEXBOT_PROVIDER=tradier")
        return TradierProvider(
            base_url=settings.tradier_base_url,
            token=settings.tradier_token,
            cache_ttl_seconds=settings.chain_refresh_seconds,
        )
    if settings.provider != "mock":
        raise RuntimeError(f"unsupported GEXBOT_PROVIDER {settings.provider!r}")
    return MockMarketDataProvider(now=datetime.now(timezone.utc))


def runtime_run_to_stored(run: RuntimeAnalysisRun) -> StoredAnalysisRun:
    return StoredAnalysisRun(
        id=run.id,
        input_data=run.input_data,
        result=run.result,
        persist_strike_rows=run.persist_strike_rows,
    )


def _runtime_from_persisted(persisted: PersistedAnalysisRun) -> RuntimeAnalysisRun:
    result = persisted.result or analyze_gex_proxy(persisted.input_data)
    if persisted.warnings:
        result = AnalysisResult(
            rows=result.rows,
            net_gex_proxy=result.net_gex_proxy,
            zero_gamma_proxy=result.zero_gamma_proxy,
            call_wall_proxy=result.call_wall_proxy,
            put_wall_proxy=result.put_wall_proxy,
            excluded_contract_count=result.excluded_contract_count,
            warnings=tuple(sorted(set(result.warnings + persisted.warnings))),
            model_inputs=result.model_inputs,
        )
    return RuntimeAnalysisRun(
        id=persisted.id,
        chain_capture_id=persisted.chain_capture_id,
        chain_capture=persisted.chain_capture,
        spot=persisted.spot,
        input_data=persisted.input_data,
        result=result,
        persist_strike_rows=persisted.persist_strike_rows,
    )
