"""Tests covering the reliability/safety fixes.

- Thread-safe in-memory mode (parallel ops, concurrent same idempotency key).
- Ticker eligibility rejection before provider access.
- Optional API-key enforcement on mutating endpoints.
- Private-by-default configuration and Docker exposure.
- Tradier mode fails clearly when the token is missing (no secret leakage).
"""

from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from app.providers.mock import MockMarketDataProvider
from app.services.capture import ChainCaptureRequest
from app.services.orchestrator import RuntimeState, TickerNotEnabledError, make_provider


def build_settings(**overrides) -> Settings:
    base = dict(
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
        database_url=None,
        cors_origins=("http://localhost:3000",),
        auto_ensure_schema=False,
    )
    base.update(overrides)
    return Settings(**base)


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


class InMemoryConcurrencyTest(unittest.TestCase):
    def _state_with_capture(self) -> RuntimeState:
        provider = MockMarketDataProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc))
        state = RuntimeState(settings=build_settings(), provider=provider)
        state.capture_chain(ChainCaptureRequest(ticker="SPY", min_dte=0, max_dte=7))
        return state

    def test_parallel_analyses_do_not_lose_entries_or_duplicate_ids(self):
        state = self._state_with_capture()

        def run(i: int):
            return state.analyze_latest(
                ticker="SPY",
                scope="all",
                persist_strike_rows=False,
                idempotency_key=f"key-{i}",
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            runs = list(pool.map(run, range(60)))

        ids = {run.id for run in runs}
        self.assertEqual(len(ids), 60, "each distinct-key analysis must get a unique id")
        self.assertEqual(len(state.analysis_runs), 60, "no runs lost or duplicated")

    def test_concurrent_same_idempotency_key_yields_single_logical_run(self):
        state = self._state_with_capture()

        def run(_: int):
            return state.analyze_latest(
                ticker="SPY",
                scope="all",
                persist_strike_rows=False,
                idempotency_key="shared-key",
            )

        with ThreadPoolExecutor(max_workers=16) as pool:
            runs = list(pool.map(run, range(32)))

        ids = {run.id for run in runs}
        self.assertEqual(len(ids), 1, "all same-key requests converge on one run id")
        self.assertEqual(len(state.analysis_runs), 1, "exactly one logical run is created")

    def test_parallel_ticker_registration_keeps_all_entries(self):
        state = RuntimeState(
            settings=build_settings(),
            provider=MockMarketDataProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)),
        )
        symbols = [f"AA{i:02d}" for i in range(40)]

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda s: state.add_ticker(s, 0.0), symbols))

        registered = set(state.ticker_symbols())
        for symbol in symbols:
            self.assertIn(symbol, registered)


class TickerEligibilityTest(unittest.TestCase):
    def _client(self, provider):
        state = RuntimeState(settings=build_settings(), provider=provider)
        return state, TestClient(create_app(state))

    def test_capture_rejects_unregistered_ticker_before_provider(self):
        provider = CountingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc))
        _, client = self._client(provider)

        response = client.post("/api/v1/chains/capture", json={"ticker": "AAPL", "min_dte": 0, "max_dte": 0})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "ticker_not_enabled")
        self.assertEqual(provider.available_expiration_calls, 0)
        self.assertEqual(provider.option_chain_calls, 0)

    def test_analyze_rejects_unregistered_ticker_before_provider(self):
        provider = CountingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc))
        _, client = self._client(provider)

        response = client.post(
            "/api/v1/gex-proxy/analyze",
            json={"ticker": "AAPL", "scope": "all", "persist_strike_rows": False, "allow_chain_capture": True},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "ticker_not_enabled")
        self.assertEqual(provider.available_expiration_calls, 0)
        self.assertEqual(provider.spot_quote_calls, 0)

    def test_capture_rejects_disabled_ticker_before_provider(self):
        provider = CountingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc))
        state, client = self._client(provider)
        state.update_ticker("SPY", enabled=False)

        response = client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 0, "max_dte": 0})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(provider.available_expiration_calls, 0)

    def test_runtime_state_raises_ticker_not_enabled_directly(self):
        provider = CountingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc))
        state = RuntimeState(settings=build_settings(), provider=provider)
        with self.assertRaises(TickerNotEnabledError):
            state.capture_chain(ChainCaptureRequest(ticker="AAPL", min_dte=0, max_dte=0))
        self.assertEqual(provider.available_expiration_calls, 0)


class ApiKeyGuardTest(unittest.TestCase):
    def test_mutating_endpoint_requires_key_when_configured(self):
        state = RuntimeState(
            settings=build_settings(api_key="s3cret"),
            provider=MockMarketDataProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)),
        )
        client = TestClient(create_app(state))

        missing = client.post("/api/v1/tickers", json={"ticker": "MSFT"})
        wrong = client.post("/api/v1/tickers", json={"ticker": "MSFT"}, headers={"X-API-Key": "nope"})
        correct = client.post("/api/v1/tickers", json={"ticker": "MSFT"}, headers={"X-API-Key": "s3cret"})
        read = client.get("/api/v1/tickers")

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json()["error"]["code"], "unauthorized")
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(correct.status_code, 200)
        self.assertEqual(read.status_code, 200)

    def test_no_key_configured_allows_local_mutations(self):
        state = RuntimeState(
            settings=build_settings(),
            provider=MockMarketDataProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)),
        )
        client = TestClient(create_app(state))

        response = client.post("/api/v1/tickers", json={"ticker": "MSFT"})

        self.assertEqual(response.status_code, 200)


class ReadinessEndpointTest(unittest.TestCase):
    def test_health_and_ready_do_not_call_provider(self):
        provider = CountingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc))
        state = RuntimeState(settings=build_settings(), provider=provider)
        client = TestClient(create_app(state))

        health = client.get("/health")
        ready = client.get("/ready")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"ok": True})
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["checks"]["storage"], "in-memory")
        self.assertEqual(ready.json()["checks"]["provider"], "mock")
        self.assertEqual(provider.available_expiration_calls, 0)
        self.assertEqual(provider.spot_quote_calls, 0)


class PrivateExposureConfigTest(unittest.TestCase):
    def test_config_defaults_are_private_and_pool_sized(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = get_settings()

        self.assertEqual(settings.bind_host, "127.0.0.1")
        self.assertIsNone(settings.api_key)
        self.assertEqual(settings.db_pool_min_size, 1)
        self.assertEqual(settings.db_pool_max_size, 5)
        self.assertEqual(settings.db_pool_timeout_seconds, 10.0)

    def test_pool_bounds_are_validated(self):
        with patch.dict(os.environ, {"DB_POOL_MIN_SIZE": "9", "DB_POOL_MAX_SIZE": "3"}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()
        self.assertIn("DB_POOL_MIN_SIZE must be less than or equal to DB_POOL_MAX_SIZE", str(raised.exception))

        with patch.dict(os.environ, {"DB_POOL_TIMEOUT_SECONDS": "0"}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()
        self.assertIn("DB_POOL_TIMEOUT_SECONDS must be positive", str(raised.exception))

    def test_compose_binds_services_to_localhost_and_hides_postgres(self):
        compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()

        self.assertIn('"${GEXBOT_BIND_HOST:-127.0.0.1}:8000:8000"', compose)
        self.assertIn('"${GEXBOT_BIND_HOST:-127.0.0.1}:3000:3000"', compose)
        # PostgreSQL must not be published to the host.
        self.assertNotIn('"5432:5432"', compose)


class TradierConfigTest(unittest.TestCase):
    def test_make_provider_requires_token_for_tradier(self):
        settings = build_settings(provider="tradier", tradier_token=None)
        with self.assertRaises(RuntimeError) as raised:
            make_provider(settings)
        self.assertIn("TRADIER_TOKEN is required", str(raised.exception))

    def test_get_settings_error_does_not_leak_token(self):
        with patch.dict(os.environ, {"GEXBOT_PROVIDER": "tradier", "TRADIER_TOKEN": "  "}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()
        message = str(raised.exception)
        self.assertIn("TRADIER_TOKEN is required when GEXBOT_PROVIDER=tradier", message)


if __name__ == "__main__":
    unittest.main()
