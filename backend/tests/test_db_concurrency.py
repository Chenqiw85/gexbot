"""Real-PostgreSQL concurrency integration tests.

These require a running PostgreSQL and are skipped unless
``GEXBOT_TEST_DATABASE_URL`` is set, e.g.::

    docker run -d --name gexbot-test-pg -e POSTGRES_USER=gexbot \\
      -e POSTGRES_PASSWORD=gexbot -e POSTGRES_DB=gexbot -p 55432:5432 postgres:16
    GEXBOT_TEST_DATABASE_URL=postgresql://gexbot:gexbot@127.0.0.1:55432/gexbot \\
      python -m unittest discover -s tests

They deliberately use a real database (not mocked cursors) so that the
transaction-race behaviour of the connection pool and the idempotency upsert is
exercised for real.
"""

from __future__ import annotations

import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from uuid import uuid4

from app.domain.gex import (
    AnalysisInput,
    ContractSnapshot,
    OptionType,
    PositionAssumption,
    analyze_gex_proxy,
)
from app.providers.base import SpotQuote
from app.db.locks import PostgresLeaseLockStore
from app.services.capture import ChainCapture

try:  # optional dependency: tests are skipped entirely when unavailable
    from app.db.repository import PostgresRepository
except Exception:  # pragma: no cover
    PostgresRepository = None  # type: ignore

TEST_DATABASE_URL = os.getenv("GEXBOT_TEST_DATABASE_URL")


def _analyzed_at() -> datetime:
    return datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)


def _analysis_input_with_row() -> AnalysisInput:
    analyzed_at = _analyzed_at()
    close = datetime(2026, 7, 31, 16, 0, tzinfo=timezone.utc)
    contract = ContractSnapshot(
        option_symbol="SPY260731C00500000",
        strike=500.0,
        option_type=OptionType.CALL,
        open_interest=100,
        volume=10,
        implied_volatility=0.2,
        iv_observed_at=None,
        oi_observed_date=None,
        contract_multiplier=100,
        underlying_close_at=close,
        option_last_trade_at=close,
    )
    return AnalysisInput(
        ticker="SPY",
        scope="all",
        spot_price=500.0,
        spot_observed_at=analyzed_at,
        spot_provider_received_at=analyzed_at,
        analyzed_at=analyzed_at,
        risk_free_rate=0.05,
        dividend_yield=0.0,
        position_assumption=PositionAssumption.CALL_POSITIVE_PUT_NEGATIVE,
        contracts=[contract],
    )


@unittest.skipUnless(TEST_DATABASE_URL, "set GEXBOT_TEST_DATABASE_URL to run PostgreSQL concurrency tests")
class PostgresConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.repo = PostgresRepository.connect(TEST_DATABASE_URL, min_size=1, max_size=6, timeout=10.0)
        self.repo.ensure_schema()

    def tearDown(self):
        self.repo.close()

    def _seed_capture_and_spot(self) -> tuple[int, int]:
        now = _analyzed_at()
        capture = ChainCapture(
            ticker="SPY",
            source="test",
            status="success",
            min_dte=0,
            max_dte=7,
            requested_expirations=[date(2026, 7, 31)],
            chain_captured_at=now,
            provider_received_at=now,
            expiration_captures=[],
            warnings=(),
        )
        chain_id = self.repo.save_chain_capture(capture)
        spot_id = self.repo.save_spot_observation(
            SpotQuote(ticker="SPY", spot_price=500.0, spot_observed_at=now, provider_received_at=now),
            "test",
        )
        return chain_id, spot_id

    def test_parallel_operations_use_distinct_connections(self):
        # Force four operations to hold their connection simultaneously; if the
        # repository shared one connection this could not yield four distinct
        # backend PIDs.
        workers = 4
        barrier = threading.Barrier(workers)
        pids: list[int] = []
        pids_lock = threading.Lock()

        def op(_):
            with self.repo.checkout() as conn:
                pid = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
                barrier.wait()  # all four hold a connection at once
                with pids_lock:
                    pids.append(pid)
                barrier.wait()  # keep holding until everyone has recorded

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(op, range(workers)))

        self.assertEqual(len(set(pids)), workers, "each concurrent op must own a distinct connection")

    def test_concurrent_same_idempotency_key_creates_exactly_one_run(self):
        chain_id, spot_id = self._seed_capture_and_spot()
        key = f"race-{uuid4()}"
        input_data = _analysis_input_with_row()
        result = analyze_gex_proxy(input_data)

        def insert(_):
            return self.repo.save_analysis_run(
                chain_capture_id=chain_id,
                spot_observation_id=spot_id,
                input_data=input_data,
                result=result,
                persist_strike_rows=False,
                warnings=(),
                min_dte=0,
                max_dte=7,
                idempotency_key=key,
            )

        with ThreadPoolExecutor(max_workers=16) as pool:
            ids = list(pool.map(insert, range(24)))

        self.assertEqual(len(set(ids)), 1, "all concurrent same-key inserts return one id")
        self.assertIsNotNone(ids[0])
        with self.repo.checkout() as conn:
            count = conn.execute(
                "SELECT count(*) FROM analysis_runs WHERE idempotency_key = %s", (key,)
            ).fetchone()[0]
        self.assertEqual(count, 1, "exactly one logical run row exists for the key")

    def test_idempotent_insert_writes_strike_rows_only_once(self):
        chain_id, spot_id = self._seed_capture_and_spot()
        key = f"once-{uuid4()}"
        input_data = _analysis_input_with_row()
        result = analyze_gex_proxy(input_data)
        self.assertGreater(len(result.rows), 0)

        first = self.repo.save_analysis_run(
            chain_capture_id=chain_id,
            spot_observation_id=spot_id,
            input_data=input_data,
            result=result,
            persist_strike_rows=True,
            warnings=(),
            min_dte=0,
            max_dte=7,
            idempotency_key=key,
        )
        second = self.repo.save_analysis_run(
            chain_capture_id=chain_id,
            spot_observation_id=spot_id,
            input_data=input_data,
            result=result,
            persist_strike_rows=True,
            warnings=(),
            min_dte=0,
            max_dte=7,
            idempotency_key=key,
        )

        self.assertEqual(first, second, "second insert returns the existing run id")
        with self.repo.checkout() as conn:
            strike_count = conn.execute(
                "SELECT count(*) FROM strike_gex_proxy WHERE analysis_run_id = %s", (first,)
            ).fetchone()[0]
        self.assertEqual(
            strike_count,
            len(result.rows),
            "the no-op conflict update must not re-insert (duplicate) strike rows",
        )

    def test_scheduler_lock_and_manual_analysis_run_concurrently(self):
        # A scheduler lease acquisition and a manual analysis-run insert running
        # at the same time must not corrupt each other's transaction boundaries.
        chain_id, spot_id = self._seed_capture_and_spot()
        lock_store = PostgresLeaseLockStore(self.repo)
        lock_key = f"chain_capture:SPY:{uuid4()}"
        now = _analyzed_at()
        input_data = _analysis_input_with_row()
        result = analyze_gex_proxy(input_data)

        from datetime import timedelta

        lock_results: list[bool] = []
        lock_lock = threading.Lock()
        run_ids: list[int] = []
        run_lock = threading.Lock()

        def acquire(owner: int):
            ok = lock_store.acquire(lock_key, f"owner-{owner}", now, timedelta(seconds=30))
            with lock_lock:
                lock_results.append(ok)

        def analyze(i: int):
            run_id = self.repo.save_analysis_run(
                chain_capture_id=chain_id,
                spot_observation_id=spot_id,
                input_data=input_data,
                result=result,
                persist_strike_rows=False,
                warnings=(),
                min_dte=0,
                max_dte=7,
                idempotency_key=f"manual-{uuid4()}-{i}",
            )
            with run_lock:
                run_ids.append(run_id)

        tasks = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for owner in range(4):
                tasks.append(pool.submit(acquire, owner))
            for i in range(4):
                tasks.append(pool.submit(analyze, i))
            for task in tasks:
                task.result()  # re-raise any transaction-boundary error

        self.assertEqual(sum(1 for ok in lock_results if ok), 1, "exactly one owner wins the lease")
        self.assertEqual(len(run_ids), 4, "all concurrent manual analyses committed")
        self.assertEqual(len(set(run_ids)), 4, "each manual analysis is a distinct run")


if __name__ == "__main__":
    unittest.main()
