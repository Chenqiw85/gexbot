import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app as module_app
from app.main import create_app
from app.providers.base import ProviderDataError
from app.providers.http import HttpRequestError
from app.providers.mock import MockMarketDataProvider
from app.services.history import UnsupportedModelVersion
from app.services.orchestrator import RuntimeState


NY = ZoneInfo("America/New_York")


def test_settings() -> Settings:
    return Settings(
        provider="mock",
        tradier_base_url="https://api.tradier.com/v1",
        tradier_token=None,
        default_tickers=("SPY", "QQQ"),
        default_risk_free_rate=0.05,
        default_dividend_yield=0.0,
        default_min_dte=0,
        default_max_dte=45,
        default_max_expirations=12,
        chain_refresh_seconds=900,
        spot_refresh_seconds=15,
        database_url=None,
        cors_origins=("http://localhost:3000",),
        auto_ensure_schema=True,
    )


class ApiIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.settings = test_settings()
        self.provider = MockMarketDataProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))
        self.state = RuntimeState(settings=self.settings, provider=self.provider)
        self.client = TestClient(create_app(self.state))

    def test_custom_ticker_capture_analyze_history_and_recompute_workflow(self):
        created = self.client.post("/api/v1/tickers", json={"ticker": "aapl", "dividend_yield": 0.006})
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["ticker"], "AAPL")

        capture = self.client.post("/api/v1/chains/capture", json={"ticker": "AAPL", "min_dte": 0, "max_dte": 7})
        self.assertEqual(capture.status_code, 200)
        self.assertEqual(capture.json()["status"], "success")
        self.assertEqual(len(capture.json()["expiration_captures"]), 2)

        fixed_now = datetime(2026, 7, 27, 10, 5, tzinfo=NY).astimezone(timezone.utc)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_now.replace(tzinfo=None)
                return fixed_now.astimezone(tz)

        with patch("app.services.orchestrator.datetime", FixedDateTime):
            analyzed = self.client.post(
                "/api/v1/gex-proxy/analyze",
                json={"ticker": "AAPL", "scope": "all", "persist_strike_rows": False},
            )
        self.assertEqual(analyzed.status_code, 200)
        body = analyzed.json()
        self.assertEqual(body["metric_name"], "gex_proxy")
        self.assertEqual(body["ticker"], "AAPL")
        self.assertEqual(body["calendar_version"], "nyse_rules_v3")
        self.assertEqual(body["chain_min_dte"], 0)
        self.assertEqual(body["chain_max_dte"], 7)
        self.assertEqual(body["chain_status"], "success")
        self.assertEqual(body["requested_expirations"], ["2026-07-27", "2026-07-31"])
        self.assertEqual(body["included_expirations"], ["2026-07-27", "2026-07-31"])
        self.assertEqual(body["persist_strike_rows"], False)
        self.assertNotIn("rows", body)

        latest = self.client.get("/api/v1/gex-proxy/latest", params={"ticker": "AAPL", "scope": "all", "include_rows": True})
        self.assertEqual(latest.status_code, 200)
        self.assertGreater(len(latest.json()["rows"]), 0)

        history = self.client.get("/api/v1/gex-proxy/history", params={"ticker": "AAPL", "scope": "all", "date": "2026-07-27"})
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()["runs"]), 1)

        loaded = self.client.get(f"/api/v1/gex-proxy/runs/{body['id']}", params={"include_rows": True})
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["id"], body["id"])
        self.assertGreater(len(loaded.json()["rows"]), 0)

        recompute = self.client.post(f"/api/v1/gex-proxy/runs/{body['id']}/recompute")
        self.assertEqual(recompute.status_code, 200)
        self.assertEqual(recompute.json()["diff"], 0)
        self.assertEqual(recompute.json()["net_gex_proxy"]["diff"], 0)
        self.assertEqual(recompute.json()["zero_gamma_proxy"]["diff"], 0)
        self.assertEqual(recompute.json()["call_wall_proxy"]["diff"], 0)
        self.assertEqual(recompute.json()["put_wall_proxy"]["diff"], 0)
        self.assertEqual(recompute.json()["warnings"], [])

    def test_persist_rows_endpoint_marks_lightweight_run_as_checkpoint(self):
        capture = self.client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 0, "max_dte": 7})
        self.assertEqual(capture.status_code, 200)

        analyzed = self.client.post(
            "/api/v1/gex-proxy/analyze",
            json={"ticker": "SPY", "scope": "all", "persist_strike_rows": False},
        )
        self.assertEqual(analyzed.status_code, 200)
        self.assertEqual(analyzed.json()["persist_strike_rows"], False)
        self.assertNotIn("rows", analyzed.json())

        checkpoint = self.client.post(f"/api/v1/gex-proxy/runs/{analyzed.json()['id']}/persist-rows")

        self.assertEqual(checkpoint.status_code, 200)
        self.assertEqual(checkpoint.json()["persist_strike_rows"], True)
        self.assertGreater(len(checkpoint.json()["rows"]), 0)

        loaded = self.client.get(f"/api/v1/gex-proxy/runs/{analyzed.json()['id']}", params={"include_rows": False})
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["persist_strike_rows"], True)
        self.assertNotIn("rows", loaded.json())

    def test_history_endpoint_honors_limit_for_intraday_replay(self):
        capture = self.client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 0, "max_dte": 7})
        self.assertEqual(capture.status_code, 200)

        fixed_now = datetime(2026, 7, 27, 10, 5, tzinfo=NY).astimezone(timezone.utc)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_now.replace(tzinfo=None)
                return fixed_now.astimezone(tz)

        with patch("app.services.orchestrator.datetime", FixedDateTime):
            first = self.client.post(
                "/api/v1/gex-proxy/analyze",
                json={"ticker": "SPY", "scope": "all", "persist_strike_rows": False},
            )
            second = self.client.post(
                "/api/v1/gex-proxy/analyze",
                json={"ticker": "SPY", "scope": "all", "persist_strike_rows": False},
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        history = self.client.get(
            "/api/v1/gex-proxy/history",
            params={"ticker": "SPY", "scope": "all", "date": "2026-07-27", "limit": 1},
        )

        self.assertEqual(history.status_code, 200)
        self.assertEqual([item["id"] for item in history.json()["runs"]], [first.json()["id"]])

    def test_analysis_runs_expose_shared_chain_capture_id_for_replay(self):
        capture = self.client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 0, "max_dte": 7})
        self.assertEqual(capture.status_code, 200)

        first = self.client.post(
            "/api/v1/gex-proxy/analyze",
            json={"ticker": "SPY", "scope": "all", "persist_strike_rows": False},
        )
        second = self.client.post(
            "/api/v1/gex-proxy/analyze",
            json={"ticker": "SPY", "scope": "0dte", "persist_strike_rows": False},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertIsNotNone(first.json().get("chain_capture_id"))
        self.assertEqual(first.json().get("chain_capture_id"), second.json().get("chain_capture_id"))
        self.assertEqual(first.json()["chain_captured_at"], second.json()["chain_captured_at"])

    def test_latest_is_read_only_when_no_run_exists(self):
        provider = CountingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))
        state = RuntimeState(settings=self.settings, provider=provider)
        client = TestClient(create_app(state))

        response = client.get("/api/v1/gex-proxy/latest", params={"ticker": "SPY", "scope": "all"})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "no_latest_run")
        self.assertEqual(provider.available_expiration_calls, 0)
        self.assertEqual(provider.option_chain_calls, 0)
        self.assertEqual(provider.spot_quote_calls, 0)

    def test_analyze_uses_existing_capture_only_by_default(self):
        provider = CountingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))
        state = RuntimeState(settings=self.settings, provider=provider)
        client = TestClient(create_app(state))

        response = client.post(
            "/api/v1/gex-proxy/analyze",
            json={"ticker": "SPY", "scope": "all", "persist_strike_rows": False},
        )

        self.assertEqual(response.status_code, 502)
        self.assertIn("no usable option chain capture", response.json()["error"]["message"])
        self.assertEqual(provider.available_expiration_calls, 0)
        self.assertEqual(provider.option_chain_calls, 0)

    def test_provider_errors_return_structured_gateway_error(self):
        provider = MockMarketDataProvider(
            now=datetime(2026, 7, 27, 10, 0, tzinfo=NY),
            failing_expirations={
                datetime(2026, 7, 27, 10, 0, tzinfo=NY).date(),
                datetime(2026, 7, 31, 10, 0, tzinfo=NY).date(),
                datetime(2026, 8, 7, 10, 0, tzinfo=NY).date(),
                datetime(2026, 9, 18, 10, 0, tzinfo=NY).date(),
            },
        )
        state = RuntimeState(settings=self.settings, provider=provider)
        client = TestClient(create_app(state))

        response = client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 0, "max_dte": 0})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "capture_failed")

    def test_http_provider_errors_include_provider_status_code(self):
        provider = HttpFailingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))
        state = RuntimeState(settings=self.settings, provider=provider)
        client = TestClient(create_app(state))

        response = client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 0, "max_dte": 0})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "provider_error")
        self.assertEqual(response.json()["error"]["provider_status_code"], 503)

    def test_empty_chain_capture_returns_structured_failure(self):
        provider = EmptyChainProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))
        state = RuntimeState(settings=self.settings, provider=provider)
        client = TestClient(create_app(state))

        response = client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 0, "max_dte": 0})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "capture_failed")
        self.assertIn("capture_failed", response.json()["error"]["warnings"])

    def test_empty_expiration_selection_returns_structured_failure_without_chain_calls(self):
        provider = FutureExpirationProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))
        state = RuntimeState(settings=self.settings, provider=provider)
        client = TestClient(create_app(state))

        response = client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 0, "max_dte": 0})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "capture_failed")
        self.assertIn("no_expirations_selected", response.json()["error"]["warnings"])
        self.assertEqual(provider.option_chain_calls, 0)

    def test_provider_data_error_during_expiration_discovery_returns_structured_gateway_error(self):
        provider = ExpirationDiscoveryErrorProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))
        state = RuntimeState(settings=self.settings, provider=provider)
        client = TestClient(create_app(state))

        response = client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 0, "max_dte": 7})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "provider_error")
        self.assertIn("bad expirations payload", response.json()["error"]["message"])

    def test_chain_capture_can_include_contract_details_for_data_quality(self):
        response = self.client.post(
            "/api/v1/chains/capture",
            params={"include_contracts": True},
            json={"ticker": "SPY", "min_dte": 0, "max_dte": 0},
        )

        self.assertEqual(response.status_code, 200)
        expiration = response.json()["expiration_captures"][0]
        contract = expiration["contracts"][0]
        self.assertIn("option_symbol", contract)
        self.assertIn("option_type", contract)
        self.assertIn("open_interest", contract)
        self.assertIn("volume", contract)
        self.assertIn("implied_volatility", contract)
        self.assertIn("iv_observed_at", contract)
        self.assertIn("oi_observed_date", contract)
        self.assertIn("contract_multiplier", contract)
        self.assertIn("underlying_close_at", contract)
        self.assertIn("option_last_trade_at", contract)
        self.assertIn("bid", contract)
        self.assertIn("ask", contract)
        self.assertIn("last", contract)
        self.assertIn("mark", contract)
        self.assertIn("vendor_delta", contract)
        self.assertIn("vendor_gamma", contract)
        self.assertIn("vendor_theta", contract)
        self.assertIn("vendor_vega", contract)

    def test_latest_chain_capture_is_read_only_when_no_capture_exists(self):
        provider = CountingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))
        state = RuntimeState(settings=self.settings, provider=provider)
        client = TestClient(create_app(state))

        response = client.get("/api/v1/chains/latest", params={"ticker": "SPY"})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json().get("error", {}).get("code"), "no_latest_chain_capture")
        self.assertEqual(provider.available_expiration_calls, 0)
        self.assertEqual(provider.option_chain_calls, 0)
        self.assertEqual(provider.spot_quote_calls, 0)

    def test_analyze_returns_gateway_error_when_initial_chain_capture_fails(self):
        provider = MockMarketDataProvider(
            now=datetime(2026, 7, 27, 10, 0, tzinfo=NY),
            failing_expirations={
                datetime(2026, 7, 27, 10, 0, tzinfo=NY).date(),
                datetime(2026, 7, 31, 10, 0, tzinfo=NY).date(),
                datetime(2026, 8, 7, 10, 0, tzinfo=NY).date(),
            },
        )
        state = RuntimeState(settings=self.settings, provider=provider)
        client = TestClient(create_app(state))

        response = client.post(
            "/api/v1/gex-proxy/analyze",
            json={"ticker": "SPY", "scope": "all", "persist_strike_rows": False, "allow_chain_capture": True},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "provider_error")
        self.assertIn("initial option chain capture failed", response.json()["error"]["message"])

    def test_failed_chain_capture_does_not_replace_last_usable_memory_capture(self):
        first_capture = self.client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 0, "max_dte": 0})
        self.assertEqual(first_capture.status_code, 200)
        self.assertEqual(first_capture.json()["status"], "success")

        self.provider.failing_expirations = {datetime(2026, 7, 27, 10, 0, tzinfo=NY).date()}
        failed_capture = self.client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 0, "max_dte": 0})
        self.assertEqual(failed_capture.status_code, 502)

        fixed_now = datetime(2026, 7, 27, 10, 5, tzinfo=NY).astimezone(timezone.utc)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_now.replace(tzinfo=None)
                return fixed_now.astimezone(tz)

        with patch("app.services.orchestrator.datetime", FixedDateTime):
            analyzed = self.client.post(
                "/api/v1/gex-proxy/analyze",
                json={"ticker": "SPY", "scope": "all", "persist_strike_rows": True},
            )

        self.assertEqual(analyzed.status_code, 200)
        self.assertEqual(analyzed.json()["chain_status"], "success")
        self.assertGreater(len(analyzed.json()["rows"]), 0)
        self.assertNotIn("capture_failed", analyzed.json()["warnings"])

    def test_partial_capture_analysis_warnings_survive_replay_endpoints(self):
        provider = MockMarketDataProvider(
            now=datetime(2026, 7, 27, 10, 0, tzinfo=NY),
            failing_expirations={datetime(2026, 7, 31, 10, 0, tzinfo=NY).date()},
        )
        state = RuntimeState(settings=self.settings, provider=provider)
        client = TestClient(create_app(state))
        capture = client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 0, "max_dte": 7})
        self.assertEqual(capture.status_code, 200)
        self.assertEqual(capture.json()["status"], "partial")

        analyzed = client.post(
            "/api/v1/gex-proxy/analyze",
            json={"ticker": "SPY", "scope": "all", "persist_strike_rows": False},
        )

        self.assertEqual(analyzed.status_code, 200)
        self.assertIn("partial_capture", analyzed.json()["warnings"])
        self.assertIn("analysis_uses_partial_capture", analyzed.json()["warnings"])

        latest = client.get("/api/v1/gex-proxy/latest", params={"ticker": "SPY", "scope": "all"})
        history = client.get("/api/v1/gex-proxy/history", params={"ticker": "SPY", "scope": "all", "date": "2026-07-27"})
        loaded = client.get(f"/api/v1/gex-proxy/runs/{analyzed.json()['id']}")
        recompute = client.post(f"/api/v1/gex-proxy/runs/{analyzed.json()['id']}/recompute")

        for response in (latest, loaded):
            self.assertEqual(response.status_code, 200)
            self.assertIn("partial_capture", response.json()["warnings"])
            self.assertIn("analysis_uses_partial_capture", response.json()["warnings"])
        self.assertEqual(history.status_code, 200)
        self.assertIn("partial_capture", history.json()["runs"][0]["warnings"])
        self.assertIn("analysis_uses_partial_capture", history.json()["runs"][0]["warnings"])
        self.assertEqual(recompute.status_code, 200)
        self.assertIn("partial_capture", recompute.json()["warnings"])
        self.assertIn("analysis_uses_partial_capture", recompute.json()["warnings"])

    def test_expired_contracts_are_not_reported_as_included_expirations(self):
        capture = self.client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 0, "max_dte": 0})
        self.assertEqual(capture.status_code, 200)

        fixed_now = datetime(2026, 7, 27, 16, 16, tzinfo=NY).astimezone(timezone.utc)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_now.replace(tzinfo=None)
                return fixed_now.astimezone(tz)

        with patch("app.services.orchestrator.datetime", FixedDateTime):
            analyzed = self.client.post(
                "/api/v1/gex-proxy/analyze",
                json={"ticker": "SPY", "scope": "all", "persist_strike_rows": True},
            )

        body = analyzed.json()
        self.assertEqual(analyzed.status_code, 200)
        self.assertEqual(body["included_expirations"], [])
        self.assertIsNone(body["zero_gamma_proxy"])
        self.assertGreater(body["excluded_contract_count"], 0)
        self.assertEqual(body["rows"], [])

    def test_recompute_returns_structured_error_for_unsupported_model_version(self):
        capture = self.client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 0, "max_dte": 7})
        self.assertEqual(capture.status_code, 200)
        analyzed = self.client.post(
            "/api/v1/gex-proxy/analyze",
            json={"ticker": "SPY", "scope": "all", "persist_strike_rows": False},
        )
        self.assertEqual(analyzed.status_code, 200)

        with patch(
            "app.main.recompute_from_record",
            side_effect=UnsupportedModelVersion("unsupported model_version black_scholes_v2.0.0"),
        ):
            response = self.client.post(f"/api/v1/gex-proxy/runs/{analyzed.json()['id']}/recompute")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "unsupported_model_version")
        self.assertIn("black_scholes_v2.0.0", response.json()["error"]["message"])

    def test_capture_rejects_invalid_dte_ranges(self):
        negative = self.client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": -1, "max_dte": 0})
        inverted = self.client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 10, "max_dte": 0})
        effective_inverted = self.client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": 60})

        self.assertEqual(negative.status_code, 422)
        self.assertEqual(inverted.status_code, 422)
        self.assertEqual(effective_inverted.status_code, 422)

    def test_validation_errors_use_structured_error_envelope(self):
        invalid_body = self.client.post("/api/v1/chains/capture", json={"ticker": "SPY", "min_dte": -1, "max_dte": 0})
        invalid_query = self.client.get("/api/v1/market/status", params={"ticker": "BAD TICKER"})

        self.assertEqual(invalid_body.status_code, 422)
        self.assertEqual(invalid_body.json()["error"]["code"], "validation_error")
        self.assertIn("body.min_dte", invalid_body.json()["error"]["message"])
        self.assertEqual(invalid_body.json()["error"]["fields"][0]["path"], "body.min_dte")

        self.assertEqual(invalid_query.status_code, 422)
        self.assertEqual(invalid_query.json()["error"]["code"], "validation_error")
        self.assertIn("ticker must be 1-12 chars", invalid_query.json()["error"]["message"])

    def test_market_status_separates_underlying_close_from_etf_option_last_trade(self):
        fixed_now = datetime(2026, 7, 27, 16, 5, tzinfo=NY).astimezone(timezone.utc)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_now.replace(tzinfo=None)
                return fixed_now.astimezone(tz)

        with patch("app.main.datetime", FixedDateTime, create=True):
            response = self.client.get("/api/v1/market/status", params={"ticker": "SPY"})

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertIn("is_option_market_open", body)
        self.assertFalse(body["is_underlying_market_open"])
        self.assertTrue(body["is_option_market_open"])
        self.assertEqual(body["underlying_open_at"], "2026-07-27T09:30:00-04:00")
        self.assertEqual(body["underlying_close_at"], "2026-07-27T16:00:00-04:00")
        self.assertEqual(body["option_last_trade_at"], "2026-07-27T16:15:00-04:00")
        self.assertEqual(body["market_date"], "2026-07-27")
        self.assertEqual(body["option_last_trade_policy"], "late_option_1615")

    def test_module_app_uses_json_body_models(self):
        client = TestClient(module_app)

        response = client.post("/api/v1/tickers", json={"ticker": "MSFT"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ticker"], "MSFT")

    def test_ticker_inputs_are_trimmed_uppercased_and_validated(self):
        created = self.client.post("/api/v1/tickers", json={"ticker": " brk.b ", "dividend_yield": 0.0})
        invalid_body = self.client.post("/api/v1/tickers", json={"ticker": "BAD TICKER"})
        invalid_query = self.client.get("/api/v1/market/status", params={"ticker": "BAD TICKER"})
        invalid_latest = self.client.get("/api/v1/gex-proxy/latest", params={"ticker": "BAD TICKER"})

        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["ticker"], "BRK.B")
        self.assertEqual(invalid_body.status_code, 422)
        self.assertEqual(invalid_query.status_code, 422)
        self.assertEqual(invalid_latest.status_code, 422)

    def test_ticker_dividend_yield_must_be_between_zero_and_one(self):
        negative_create = self.client.post("/api/v1/tickers", json={"ticker": "AAPL", "dividend_yield": -0.001})
        too_large_create = self.client.post("/api/v1/tickers", json={"ticker": "AAPL", "dividend_yield": 1.001})
        negative_update = self.client.patch("/api/v1/tickers/AAPL", json={"dividend_yield": -0.001})
        too_large_update = self.client.patch("/api/v1/tickers/AAPL", json={"dividend_yield": 1.001})

        self.assertEqual(negative_create.status_code, 422)
        self.assertEqual(too_large_create.status_code, 422)
        self.assertEqual(negative_update.status_code, 422)
        self.assertEqual(too_large_update.status_code, 422)

    def test_ticker_can_be_disabled_and_reenabled(self):
        created = self.client.post("/api/v1/tickers", json={"ticker": "aapl", "dividend_yield": 0.006})
        disabled = self.client.patch("/api/v1/tickers/aapl", json={"enabled": False})
        hidden = self.client.get("/api/v1/tickers")
        reenabled = self.client.patch(
            "/api/v1/tickers/aapl",
            json={"enabled": True, "dividend_yield": 0.012},
        )
        visible = self.client.get("/api/v1/tickers")
        invalid_path = self.client.patch("/api/v1/tickers/BAD%20TICKER", json={"enabled": False})

        self.assertEqual(created.status_code, 200)
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(disabled.json()["ticker"], "AAPL")
        self.assertFalse(disabled.json()["enabled"])
        self.assertNotIn("AAPL", [ticker["ticker"] for ticker in hidden.json()["tickers"]])
        self.assertEqual(reenabled.status_code, 200)
        self.assertEqual(reenabled.json()["ticker"], "AAPL")
        self.assertTrue(reenabled.json()["enabled"])
        self.assertEqual(reenabled.json()["dividend_yield"], 0.012)
        self.assertIn("AAPL", [ticker["ticker"] for ticker in visible.json()["tickers"]])
        self.assertEqual(invalid_path.status_code, 422)

    def test_ticker_update_requires_a_field(self):
        response = self.client.patch("/api/v1/tickers/AAPL", json={})

        self.assertEqual(response.status_code, 422)

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


class EmptyChainProvider(MockMarketDataProvider):
    def option_chain_for_expiration(self, ticker: str, expiration):
        return []


class FutureExpirationProvider(MockMarketDataProvider):
    def __init__(self, *, now):
        super().__init__(now=now)
        self.option_chain_calls = 0

    def available_expirations(self, ticker):
        return [datetime(2026, 7, 31, 10, 0, tzinfo=NY).date()]

    def option_chain_for_expiration(self, ticker: str, expiration):
        self.option_chain_calls += 1
        return super().option_chain_for_expiration(ticker, expiration)


class ExpirationDiscoveryErrorProvider(MockMarketDataProvider):
    def available_expirations(self, ticker):
        raise ProviderDataError("bad expirations payload")


class HttpFailingProvider(MockMarketDataProvider):
    def available_expirations(self, ticker):
        raise HttpRequestError(path="/markets/options/expirations", status_code=503, message="maintenance")


if __name__ == "__main__":
    unittest.main()
