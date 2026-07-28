import unittest
from datetime import datetime, timezone

from app.config import Settings
from app.providers.base import SpotQuote
from app.providers.mock import MockMarketDataProvider
from app.scheduler.locks import InMemoryLeaseLockStore
from app.scheduler.worker import _run_chain_job_if_due, _run_due_jobs_once, _run_spot_job_if_due
from app.services.capture import ChainCaptureRequest
from app.services.orchestrator import RuntimeState


class FakeState:
    def __init__(self, *, fail_chain=False, fail_spot=False):
        self.settings = Settings(
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
            auto_ensure_schema=True,
        )
        self.chain_calls = 0
        self.spot_calls = 0
        self.spot_requests = []
        self.allow_chain_capture_values = []
        self.idempotency_keys = []
        self.fail_chain = fail_chain
        self.fail_spot = fail_spot

    def capture_chain(self, request):
        self.chain_calls += 1
        if self.fail_chain:
            raise RuntimeError("chain failed")
        return type("Capture", (), {"status": "success", "expiration_captures": []})()

    def analyze_latest(
        self,
        *,
        ticker,
        scope,
        persist_strike_rows,
        allow_chain_capture=True,
        idempotency_key=None,
        spot_observation_factory=None,
    ):
        self.spot_calls += 1
        self.spot_requests.append((ticker, scope, persist_strike_rows))
        self.allow_chain_capture_values.append(allow_chain_capture)
        self.idempotency_keys.append(idempotency_key)
        if self.fail_spot:
            raise RuntimeError("spot failed")
        return type("Run", (), {"id": 1, "result": type("Result", (), {"net_gex_proxy": 0})()})()


class RecordingLockStore:
    def __init__(self) -> None:
        self.keys = []

    def acquire(self, lock_key, owner_id, now, ttl):
        self.keys.append(lock_key)
        return True


class SchedulerWorkerTest(unittest.TestCase):
    def test_late_etf_option_window_allows_chain_and_spot_analysis(self):
        state = FakeState()
        locks = InMemoryLeaseLockStore()
        now = datetime(2026, 7, 27, 20, 5, tzinfo=timezone.utc)
        last_chain = {}
        last_spot = {}

        _run_chain_job_if_due(state, locks, "owner", "SPY", now, last_chain)
        _run_spot_job_if_due(state, locks, "owner", "SPY", now, last_spot)

        self.assertEqual(state.chain_calls, 1)
        self.assertEqual(state.spot_calls, 1)

    def test_after_option_last_trade_no_jobs_run(self):
        state = FakeState()
        locks = InMemoryLeaseLockStore()
        now = datetime(2026, 7, 27, 20, 30, tzinfo=timezone.utc)

        _run_chain_job_if_due(state, locks, "owner", "SPY", now, {})
        _run_spot_job_if_due(state, locks, "owner", "SPY", now, {})

        self.assertEqual(state.chain_calls, 0)
        self.assertEqual(state.spot_calls, 0)

    def test_job_errors_are_logged_without_escaping(self):
        now = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)

        with self.assertLogs("gexbot.scheduler", level="ERROR") as logs:
            _run_chain_job_if_due(FakeState(fail_chain=True), InMemoryLeaseLockStore(), "owner", "SPY", now, {})
            _run_spot_job_if_due(FakeState(fail_spot=True), InMemoryLeaseLockStore(), "owner", "SPY", now, {})

        joined = "\n".join(logs.output)
        self.assertIn("chain capture failed", joined)
        self.assertIn("spot analyze failed", joined)

    def test_failed_chain_capture_is_throttled_until_next_chain_interval(self):
        state = FakeState(fail_chain=True)
        locks = InMemoryLeaseLockStore()
        now = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)
        last_chain = {}

        with self.assertLogs("gexbot.scheduler", level="ERROR"):
            _run_chain_job_if_due(state, locks, "owner", "SPY", now, last_chain)
        _run_chain_job_if_due(state, locks, "owner", "SPY", now, last_chain)

        self.assertEqual(state.chain_calls, 1)

    def test_chain_job_uses_bucketed_idempotent_lock_key(self):
        state = FakeState()
        locks = RecordingLockStore()
        now = datetime(2026, 7, 27, 14, 0, 7, tzinfo=timezone.utc)

        _run_chain_job_if_due(state, locks, "owner", "SPY", now, {})

        self.assertEqual(
            locks.keys,
            ["chain_capture:SPY:0-45:max12:2026-07-27T14:00:00+00:00"],
        )

    def test_failed_spot_analysis_is_throttled_until_next_spot_interval(self):
        state = FakeState(fail_spot=True)
        locks = InMemoryLeaseLockStore()
        now = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)
        last_spot = {}

        with self.assertLogs("gexbot.scheduler", level="ERROR"):
            _run_spot_job_if_due(state, locks, "owner", "SPY", now, last_spot, scope="all")
        _run_spot_job_if_due(state, locks, "owner", "SPY", now, last_spot, scope="all")

        self.assertEqual(state.spot_calls, 1)

    def test_scheduler_can_iterate_dynamic_ticker_symbols(self):
        state = FakeState()
        state.ticker_symbols = lambda: ["SPY", "AAPL"]
        locks = InMemoryLeaseLockStore()
        now = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)
        last_chain = {}
        last_spot = {}

        for ticker in state.ticker_symbols():
            _run_chain_job_if_due(state, locks, "owner", ticker, now, last_chain)
            _run_spot_job_if_due(state, locks, "owner", ticker, now, last_spot)

        self.assertEqual(state.chain_calls, 2)
        self.assertEqual(state.spot_calls, 2)

    def test_scheduler_run_once_reads_runtime_ticker_registry(self):
        state = FakeState()
        state.ticker_symbols = lambda: ["SPY", "AAPL"]
        locks = InMemoryLeaseLockStore()
        now = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)

        _run_due_jobs_once(state, locks, "owner", now, {}, {})

        self.assertEqual(state.chain_calls, 2)
        self.assertEqual(state.spot_calls, 4)
        self.assertEqual(
            state.spot_requests,
            [
                ("SPY", "all", False),
                ("SPY", "0dte", False),
                ("AAPL", "all", False),
                ("AAPL", "0dte", False),
            ],
        )

    def test_scheduler_run_once_refreshes_all_and_0dte_analysis_scopes(self):
        state = FakeState()
        state.ticker_symbols = lambda: ["SPY"]
        locks = InMemoryLeaseLockStore()
        now = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)

        _run_due_jobs_once(state, locks, "owner", now, {}, {})

        self.assertEqual(
            state.spot_requests,
            [
                ("SPY", "all", False),
                ("SPY", "0dte", False),
            ],
        )

    def test_scheduler_reuses_one_spot_quote_for_all_scopes_in_same_refresh_bucket(self):
        provider = IncrementingSpotProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc))
        state = RuntimeState(
            settings=FakeState().settings,
            provider=provider,
        )
        state.capture_chain(ChainCaptureRequest(ticker="SPY", min_dte=0, max_dte=7))
        provider.spot_quote_calls = 0
        now = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)

        _run_due_jobs_once(state, InMemoryLeaseLockStore(), "owner", now, {"SPY": now.timestamp()}, {})

        self.assertEqual(provider.spot_quote_calls, 1)
        runs = list(state.analysis_runs.values())
        self.assertEqual({run.input_data.scope for run in runs}, {"all", "0dte"})
        self.assertEqual({run.input_data.spot_price for run in runs}, {501.0})

    def test_scheduler_spot_job_uses_existing_chain_capture_only(self):
        state = FakeState()
        locks = InMemoryLeaseLockStore()
        now = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)

        _run_spot_job_if_due(state, locks, "owner", "SPY", now, {}, scope="all")

        self.assertEqual(state.allow_chain_capture_values, [False])

    def test_scheduler_spot_job_passes_bucketed_idempotency_key(self):
        state = FakeState()
        locks = InMemoryLeaseLockStore()
        now = datetime(2026, 7, 27, 14, 0, 7, tzinfo=timezone.utc)

        _run_spot_job_if_due(state, locks, "owner", "SPY", now, {}, scope="0dte")

        self.assertEqual(state.idempotency_keys, ["spot_analyze:SPY:0dte:2026-07-27T14:00:00+00:00"])


class IncrementingSpotProvider(MockMarketDataProvider):
    def __init__(self, *, now: datetime) -> None:
        super().__init__(now=now)
        self.spot_quote_calls = 0

    def spot_quote(self, ticker: str) -> SpotQuote:
        self.spot_quote_calls += 1
        return SpotQuote(
            ticker=ticker.upper(),
            spot_price=500.0 + self.spot_quote_calls,
            spot_observed_at=self.now,
            provider_received_at=self.now,
        )


if __name__ == "__main__":
    unittest.main()
