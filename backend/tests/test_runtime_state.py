import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.config import Settings
from app.db.repository import PersistedAnalysisRun
from app.domain.gex import AnalysisInput, PositionAssumption
from app.providers.base import ProviderDataError, SpotQuote
from app.providers.mock import MockMarketDataProvider
from app.services.capture import ChainCapture, ChainCaptureRequest
from app.services.orchestrator import AppTicker, RuntimeState, make_provider


NY = ZoneInfo("America/New_York")


def settings(*, auto_ensure_schema: bool, database_url: str | None = "postgresql://example"):
    return Settings(
        provider="mock",
        tradier_base_url="https://api.tradier.com/v1",
        tradier_token=None,
        default_tickers=("SPY",),
        default_risk_free_rate=0.05,
        default_dividend_yield=0.0,
        default_min_dte=0,
        default_max_dte=45,
        default_max_expirations=12,
        chain_refresh_seconds=900,
        spot_refresh_seconds=15,
        database_url=database_url,
        cors_origins=("http://localhost:3000",),
        auto_ensure_schema=auto_ensure_schema,
    )


class FakeRepository:
    ensure_calls = 0

    @classmethod
    def connect(cls, database_url):
        return cls()

    def ensure_schema(self):
        type(self).ensure_calls += 1

    def ensure_underlying(self, ticker, dividend_yield):
        pass

    def list_underlyings(self):
        return []


class RuntimeStateTest(unittest.TestCase):
    def test_schema_initialization_can_be_disabled_for_docker_init_sql(self):
        FakeRepository.ensure_calls = 0
        with patch("app.services.orchestrator.PostgresRepository", FakeRepository):
            RuntimeState(settings=settings(auto_ensure_schema=False), provider=MockMarketDataProvider)

        self.assertEqual(FakeRepository.ensure_calls, 0)

    def test_schema_initialization_runs_when_enabled(self):
        FakeRepository.ensure_calls = 0
        with patch("app.services.orchestrator.PostgresRepository", FakeRepository):
            RuntimeState(settings=settings(auto_ensure_schema=True), provider=MockMarketDataProvider)

        self.assertEqual(FakeRepository.ensure_calls, 1)

    def test_make_provider_rejects_unsupported_provider_settings(self):
        unsupported = settings(auto_ensure_schema=False, database_url=None)
        unsupported = unsupported.__class__(
            **{
                **unsupported.__dict__,
                "provider": "tradreir",
            }
        )

        with self.assertRaises(RuntimeError) as raised:
            make_provider(unsupported)

        self.assertIn("unsupported GEXBOT_PROVIDER", str(raised.exception))


class PersistentTickerRepository:
    def __init__(self):
        self.records = {}

    def ensure_schema(self):
        pass

    def ensure_underlying(self, ticker, dividend_yield):
        self.records.setdefault(ticker.upper(), AppTicker(ticker=ticker.upper(), dividend_yield=dividend_yield))

    def upsert_underlying(self, ticker, dividend_yield):
        self.records[ticker.upper()] = AppTicker(ticker=ticker.upper(), dividend_yield=dividend_yield)
        return self.records[ticker.upper()]

    def update_underlying(self, ticker, *, enabled, dividend_yield, default_dividend_yield):
        normalized = ticker.upper()
        existing = self.records.get(normalized)
        resolved_dividend_yield = existing.dividend_yield if existing else default_dividend_yield
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
        self.records[normalized] = record
        return record

    def list_underlyings(self):
        return sorted((record for record in self.records.values() if record.enabled), key=lambda item: item.ticker)


class RuntimeTickerRegistryTest(unittest.TestCase):
    def test_default_and_custom_tickers_are_repository_backed(self):
        repository = PersistentTickerRepository()

        first_state = RuntimeState(
            settings=settings(auto_ensure_schema=False),
            provider=MockMarketDataProvider,
            repository=repository,
        )
        first_state.add_ticker("aapl", 0.006)

        second_state = RuntimeState(
            settings=settings(auto_ensure_schema=False),
            provider=MockMarketDataProvider,
            repository=repository,
        )

        tickers = [item.ticker for item in second_state.list_tickers()]
        self.assertEqual(tickers, ["AAPL", "SPY"])
        self.assertEqual(second_state.ticker_symbols(), ["AAPL", "SPY"])

    def test_disabled_repository_tickers_are_not_scheduled(self):
        repository = PersistentTickerRepository()
        state = RuntimeState(
            settings=settings(auto_ensure_schema=False),
            provider=MockMarketDataProvider,
            repository=repository,
        )

        disabled = state.update_ticker("spy", enabled=False)
        reloaded = RuntimeState(
            settings=settings(auto_ensure_schema=False),
            provider=MockMarketDataProvider,
            repository=repository,
        )

        self.assertFalse(disabled.enabled)
        self.assertEqual(reloaded.list_tickers(), [])
        self.assertEqual(reloaded.ticker_symbols(), [])

        enabled = reloaded.update_ticker("spy", enabled=True, dividend_yield=0.012)

        self.assertTrue(enabled.enabled)
        self.assertEqual(enabled.dividend_yield, 0.012)
        self.assertEqual(reloaded.ticker_symbols(), ["SPY"])


class RuntimeAnalysisInputTest(unittest.TestCase):
    def test_spot_observed_time_stays_null_when_provider_does_not_supply_it(self):
        provider_received_at = datetime(2026, 7, 27, 10, 0, tzinfo=NY)
        provider = ProviderWithoutSpotObservedTime(now=provider_received_at)
        state = RuntimeState(
            settings=settings(auto_ensure_schema=False, database_url=None),
            provider=provider,
        )
        fixed_now = datetime(2026, 7, 27, 10, 5, tzinfo=NY).astimezone(timezone.utc)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_now.replace(tzinfo=None)
                return fixed_now.astimezone(tz)

        with patch("app.services.orchestrator.datetime", FixedDateTime):
            run = state.analyze_latest(
                ticker="SPY",
                scope="all",
                persist_strike_rows=False,
                allow_chain_capture=True,
            )

        self.assertIsNone(run.input_data.spot_observed_at)
        self.assertEqual(run.input_data.spot_provider_received_at, provider_received_at)
        self.assertIsNone(run.result.model_inputs.spot_observed_at)
        self.assertEqual(run.result.model_inputs.spot_provider_received_at, provider_received_at)

    def test_existing_capture_only_analysis_does_not_capture_chain(self):
        provider = CountingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))
        state = RuntimeState(
            settings=settings(auto_ensure_schema=False, database_url=None),
            provider=provider,
        )

        with self.assertRaises(ProviderDataError):
            state.analyze_latest(
                ticker="SPY",
                scope="all",
                persist_strike_rows=False,
                allow_chain_capture=False,
            )

        self.assertEqual(provider.available_expiration_calls, 0)
        self.assertEqual(provider.option_chain_calls, 0)

    def test_analyze_latest_requires_existing_capture_by_default(self):
        provider = CountingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))
        state = RuntimeState(
            settings=settings(auto_ensure_schema=False, database_url=None),
            provider=provider,
        )

        with self.assertRaises(ProviderDataError):
            state.analyze_latest(ticker="SPY", scope="all", persist_strike_rows=False)

        self.assertEqual(provider.available_expiration_calls, 0)
        self.assertEqual(provider.option_chain_calls, 0)

    def test_analyze_latest_reuses_in_memory_run_for_same_idempotency_key(self):
        provider = CountingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))
        state = RuntimeState(
            settings=settings(auto_ensure_schema=False, database_url=None),
            provider=provider,
        )
        state.capture_chain(ChainCaptureRequest(ticker="SPY", min_dte=0, max_dte=7))

        first = state.analyze_latest(
            ticker="SPY",
            scope="all",
            persist_strike_rows=False,
            idempotency_key="spot_analyze:SPY:all:2026-07-27T14:00:00+00:00",
        )
        second = state.analyze_latest(
            ticker="SPY",
            scope="all",
            persist_strike_rows=False,
            idempotency_key="spot_analyze:SPY:all:2026-07-27T14:00:00+00:00",
        )

        self.assertIs(first, second)
        self.assertEqual(provider.spot_quote_calls, 1)

    def test_repository_idempotency_key_short_circuits_before_provider_calls(self):
        provider = CountingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))
        repository = ExistingRunRepository(_persisted_empty_run())
        state = RuntimeState(
            settings=settings(auto_ensure_schema=False, database_url=None),
            provider=provider,
            repository=repository,
        )

        run = state.analyze_latest(
            ticker="SPY",
            scope="all",
            persist_strike_rows=False,
            idempotency_key="spot_analyze:SPY:all:2026-07-27T14:00:00+00:00",
        )

        self.assertEqual(run.id, 42)
        self.assertEqual(repository.lookup_keys, ["spot_analyze:SPY:all:2026-07-27T14:00:00+00:00"])
        self.assertEqual(provider.available_expiration_calls, 0)
        self.assertEqual(provider.option_chain_calls, 0)
        self.assertEqual(provider.spot_quote_calls, 0)

    def test_repository_idempotency_key_short_circuits_before_spot_observation_factory(self):
        provider = CountingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))
        repository = ExistingRunRepository(_persisted_empty_run())
        state = RuntimeState(
            settings=settings(auto_ensure_schema=False, database_url=None),
            provider=provider,
            repository=repository,
        )

        def fail_if_called():
            raise AssertionError("spot observation factory should not be called")

        run = state.analyze_latest(
            ticker="SPY",
            scope="all",
            persist_strike_rows=False,
            idempotency_key="spot_analyze:SPY:all:2026-07-27T14:00:00+00:00",
            spot_observation_factory=fail_if_called,
        )

        self.assertEqual(run.id, 42)
        self.assertEqual(provider.spot_quote_calls, 0)


class ProviderWithoutSpotObservedTime(MockMarketDataProvider):
    def spot_quote(self, ticker: str) -> SpotQuote:
        return SpotQuote(
            ticker=ticker.upper(),
            spot_price=500.0,
            spot_observed_at=None,
            provider_received_at=self.now,
        )


class CountingProvider(MockMarketDataProvider):
    def __init__(self, *, now: datetime) -> None:
        super().__init__(now=now)
        self.available_expiration_calls = 0
        self.option_chain_calls = 0
        self.spot_quote_calls = 0

    def available_expirations(self, ticker: str):
        self.available_expiration_calls += 1
        return super().available_expirations(ticker)

    def option_chain_for_expiration(self, ticker: str, expiration):
        self.option_chain_calls += 1
        return super().option_chain_for_expiration(ticker, expiration)

    def spot_quote(self, ticker: str):
        self.spot_quote_calls += 1
        return super().spot_quote(ticker)


class ExistingRunRepository:
    def __init__(self, run: PersistedAnalysisRun):
        self.run = run
        self.lookup_keys = []

    def ensure_schema(self):
        pass

    def ensure_underlying(self, ticker, dividend_yield):
        pass

    def list_underlyings(self):
        return [AppTicker(ticker="SPY", dividend_yield=0.0, enabled=True)]

    def analysis_run_by_idempotency_key(self, idempotency_key):
        self.lookup_keys.append(idempotency_key)
        return self.run

    def latest_chain_capture(self, ticker):
        raise AssertionError("latest_chain_capture should not be called when idempotency key exists")


def _persisted_empty_run() -> PersistedAnalysisRun:
    now = datetime(2026, 7, 27, 10, 0, tzinfo=NY)
    capture = ChainCapture(
        ticker="SPY",
        source="test",
        status="success",
        min_dte=0,
        max_dte=45,
        requested_expirations=[],
        chain_captured_at=now,
        provider_received_at=now,
        expiration_captures=[],
        warnings=(),
    )
    spot = SpotQuote(
        ticker="SPY",
        spot_price=500.0,
        spot_observed_at=None,
        provider_received_at=now,
    )
    input_data = AnalysisInput(
        ticker="SPY",
        scope="all",
        spot_price=500.0,
        spot_observed_at=None,
        spot_provider_received_at=now,
        analyzed_at=now,
        risk_free_rate=0.05,
        dividend_yield=0.0,
        position_assumption=PositionAssumption.CALL_POSITIVE_PUT_NEGATIVE,
        contracts=[],
    )
    return PersistedAnalysisRun(
        id=42,
        chain_capture=capture,
        chain_capture_id=7,
        spot=spot,
        spot_observation_id=8,
        input_data=input_data,
        persist_strike_rows=False,
        warnings=(),
    )


if __name__ == "__main__":
    unittest.main()
