import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.domain.gex import AnalysisInput, ContractSnapshot, OptionType, PositionAssumption, analyze_gex_proxy
from app.db.repository import PostgresRepository
from app.services.capture import ChainCapture, ExpirationCapture


NY = ZoneInfo("America/New_York")


class RepositoryConnectionTest(unittest.TestCase):
    def test_connect_enables_autocommit_for_cross_process_visibility(self):
        connection = object()
        with patch("app.db.repository.psycopg.connect", return_value=connection) as connect:
            repository = PostgresRepository.connect("postgresql://example")

        connect.assert_called_once_with("postgresql://example", autocommit=True)
        self.assertIs(repository.connection, connection)


class RepositoryChainCaptureTest(unittest.TestCase):
    def test_save_chain_capture_persists_provider_greeks_and_quote_fields(self):
        connection = RecordingConnection()
        repository = PostgresRepository(connection)
        captured_at = datetime(2026, 7, 27, 10, 0, tzinfo=NY)
        contract = ContractSnapshot(
            option_symbol="SPY260727C00500000",
            strike=500.0,
            option_type=OptionType.CALL,
            open_interest=100,
            volume=10,
            implied_volatility=0.2,
            iv_observed_at=None,
            oi_observed_date=None,
            contract_multiplier=100,
            underlying_close_at=captured_at,
            option_last_trade_at=captured_at,
        )
        object.__setattr__(contract, "bid", 1.1)
        object.__setattr__(contract, "ask", 1.2)
        object.__setattr__(contract, "last", 1.15)
        object.__setattr__(contract, "mark", 1.17)
        object.__setattr__(contract, "vendor_delta", 0.51)
        object.__setattr__(contract, "vendor_gamma", 0.02)
        object.__setattr__(contract, "vendor_theta", -0.03)
        object.__setattr__(contract, "vendor_vega", 0.12)
        capture = ChainCapture(
            ticker="SPY",
            source="test",
            status="success",
            min_dte=0,
            max_dte=0,
            requested_expirations=[date(2026, 7, 27)],
            chain_captured_at=captured_at,
            provider_received_at=captured_at,
            warnings=(),
            expiration_captures=[
                ExpirationCapture(
                    expiration_date=date(2026, 7, 27),
                    underlying_close_at=captured_at,
                    option_last_trade_at=captured_at,
                    status="success",
                    provider_observed_at=None,
                    error_message=None,
                    contracts=[contract],
                )
            ],
        )

        repository.save_chain_capture(capture)

        option_insert = connection.statement_containing("INSERT INTO option_contract_captures")
        sql, params = option_insert
        self.assertIn("vendor_delta", sql)
        self.assertIn("bid", sql)
        self.assertIn(0.51, params)
        self.assertIn(0.02, params)
        self.assertIn(-0.03, params)
        self.assertIn(0.12, params)
        self.assertIn(1.1, params)
        self.assertIn(1.2, params)
        self.assertIn(1.15, params)
        self.assertIn(1.17, params)

    def test_save_analysis_run_persists_capture_dte_range(self):
        connection = RecordingConnection()
        repository = PostgresRepository(connection)
        analyzed_at = datetime(2026, 7, 27, 10, 0, tzinfo=NY)
        expiration_close = datetime(2026, 8, 7, 16, 0, tzinfo=NY)
        contract = ContractSnapshot(
            option_symbol="SPY260807C00500000",
            strike=500.0,
            option_type=OptionType.CALL,
            open_interest=100,
            volume=10,
            implied_volatility=0.2,
            iv_observed_at=None,
            oi_observed_date=None,
            contract_multiplier=100,
            underlying_close_at=expiration_close,
            option_last_trade_at=expiration_close,
        )
        input_data = AnalysisInput(
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
        result = analyze_gex_proxy(input_data)

        try:
            repository.save_analysis_run(
                chain_capture_id=7,
                spot_observation_id=8,
                input_data=input_data,
                result=result,
                persist_strike_rows=False,
                warnings=(),
                min_dte=2,
                max_dte=21,
            )
        except TypeError:
            self.fail("save_analysis_run must accept and persist the capture DTE range")

        _, params = connection.statement_containing("INSERT INTO analysis_runs")
        self.assertEqual(params[5], 2)
        self.assertEqual(params[6], 21)

    def test_save_analysis_run_persists_only_actually_included_expirations(self):
        connection = RecordingConnection()
        repository = PostgresRepository(connection)
        analyzed_at = datetime(2026, 7, 27, 16, 5, tzinfo=NY)
        expired_close = datetime(2026, 7, 27, 16, 0, tzinfo=NY)
        future_close = datetime(2026, 7, 31, 16, 0, tzinfo=NY)
        expired_contract = ContractSnapshot(
            option_symbol="SPY260727C00500000",
            strike=500.0,
            option_type=OptionType.CALL,
            open_interest=100,
            volume=10,
            implied_volatility=0.2,
            iv_observed_at=None,
            oi_observed_date=None,
            contract_multiplier=100,
            underlying_close_at=expired_close,
            option_last_trade_at=expired_close,
        )
        future_contract = ContractSnapshot(
            option_symbol="SPY260731C00500000",
            strike=500.0,
            option_type=OptionType.CALL,
            open_interest=100,
            volume=10,
            implied_volatility=0.2,
            iv_observed_at=None,
            oi_observed_date=None,
            contract_multiplier=100,
            underlying_close_at=future_close,
            option_last_trade_at=future_close,
        )
        input_data = AnalysisInput(
            ticker="SPY",
            scope="all",
            spot_price=500.0,
            spot_observed_at=analyzed_at,
            spot_provider_received_at=analyzed_at,
            analyzed_at=analyzed_at,
            risk_free_rate=0.05,
            dividend_yield=0.0,
            position_assumption=PositionAssumption.CALL_POSITIVE_PUT_NEGATIVE,
            contracts=[expired_contract, future_contract],
        )
        result = analyze_gex_proxy(input_data)

        repository.save_analysis_run(
            chain_capture_id=7,
            spot_observation_id=8,
            input_data=input_data,
            result=result,
            persist_strike_rows=False,
            warnings=(),
            min_dte=0,
            max_dte=7,
        )

        _, params = connection.statement_containing("INSERT INTO analysis_runs")
        self.assertEqual(params[7], [date(2026, 7, 31)])

    def test_save_analysis_run_accepts_idempotency_key(self):
        connection = RecordingConnection()
        repository = PostgresRepository(connection)
        analyzed_at = datetime(2026, 7, 27, 10, 0, tzinfo=NY)
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
            underlying_close_at=datetime(2026, 7, 31, 16, 0, tzinfo=NY),
            option_last_trade_at=datetime(2026, 7, 31, 16, 0, tzinfo=NY),
        )
        input_data = AnalysisInput(
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
        result = analyze_gex_proxy(input_data)

        try:
            repository.save_analysis_run(
                chain_capture_id=7,
                spot_observation_id=8,
                input_data=input_data,
                result=result,
                persist_strike_rows=False,
                warnings=(),
                min_dte=0,
                max_dte=7,
                idempotency_key="spot:SPY:all:7:2026-07-27T10:00:00-04:00",
            )
        except TypeError:
            self.fail("save_analysis_run must accept an idempotency key")

        sql, params = connection.statement_containing("INSERT INTO analysis_runs")
        self.assertIn("idempotency_key", sql)
        self.assertIn("ON CONFLICT (idempotency_key)", sql)
        self.assertEqual(params[24], "spot:SPY:all:7:2026-07-27T10:00:00-04:00")

    def test_save_analysis_run_without_idempotency_key_avoids_null_conflict_lookup(self):
        connection = RecordingConnection()
        repository = PostgresRepository(connection)
        analyzed_at = datetime(2026, 7, 27, 10, 0, tzinfo=NY)
        input_data = AnalysisInput(
            ticker="SPY",
            scope="all",
            spot_price=500.0,
            spot_observed_at=analyzed_at,
            spot_provider_received_at=analyzed_at,
            analyzed_at=analyzed_at,
            risk_free_rate=0.05,
            dividend_yield=0.0,
            position_assumption=PositionAssumption.CALL_POSITIVE_PUT_NEGATIVE,
            contracts=[],
        )
        result = analyze_gex_proxy(input_data)

        repository.save_analysis_run(
            chain_capture_id=7,
            spot_observation_id=8,
            input_data=input_data,
            result=result,
            persist_strike_rows=False,
            warnings=(),
            min_dte=0,
            max_dte=7,
        )

        sql, params = connection.statement_containing("INSERT INTO analysis_runs")
        self.assertNotIn("WHERE idempotency_key = %s", sql)
        self.assertEqual(len(params), 25)
        self.assertIsNone(params[24])

    def test_analysis_run_by_idempotency_key_queries_without_side_effects(self):
        connection = HistoryRecordingConnection()
        repository = PostgresRepository(connection)

        result = repository.analysis_run_by_idempotency_key("spot:SPY:all:bucket")

        self.assertIsNone(result)
        sql, params = connection.statement_containing("idempotency_key = %s")
        self.assertIn("FROM analysis_runs", sql)
        self.assertEqual(params, ("spot:SPY:all:bucket",))

    def test_history_runs_filters_market_date_in_database(self):
        connection = HistoryRecordingConnection()
        repository = PostgresRepository(connection)

        try:
            runs = repository.history_runs("spy", "all", date_filter=date(2026, 7, 27))
        except TypeError:
            self.fail("history_runs must accept a market-date filter for database-side replay queries")

        self.assertEqual(runs, [])
        sql, params = connection.statement_containing("FROM analysis_runs")
        self.assertIn("AT TIME ZONE 'America/New_York'", sql)
        self.assertEqual(params, ("SPY", "all", date(2026, 7, 27), 2000))

    def test_load_analysis_run_filters_0dte_contracts_by_new_york_market_date(self):
        connection = LoadAnalysisRunConnection()
        repository = PostgresRepository(connection)

        run = repository.load_analysis_run(1)

        self.assertEqual([contract.option_symbol for contract in run.input_data.contracts], ["SPY260727C00500000"])

    def test_load_analysis_run_preserves_stored_summary_metrics_for_replay(self):
        connection = LoadAnalysisRunConnection()
        repository = PostgresRepository(connection)

        run = repository.load_analysis_run(1)

        self.assertEqual(run.result.net_gex_proxy, 123456.0)
        self.assertEqual(run.result.zero_gamma_proxy, 501.25)
        self.assertEqual(run.result.call_wall_proxy, 505.0)
        self.assertEqual(run.result.put_wall_proxy, 495.0)
        self.assertEqual(run.result.excluded_contract_count, 3)
        self.assertEqual(run.result.warnings, ("stored_partial_capture",))


class RecordingConnection:
    def __init__(self):
        self.statements = []
        self.next_id = 1

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.statements.append((sql, params or ()))
        if "RETURNING id" in sql:
            current = self.next_id
            self.next_id += 1
            return OneRowCursor((current,))
        return OneRowCursor(None)

    def statement_containing(self, needle):
        for sql, params in self.statements:
            if needle in sql:
                return sql, params
        raise AssertionError(f"statement containing {needle!r} not found")


class HistoryRecordingConnection:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params or ()))
        return ManyRowsCursor([])

    def statement_containing(self, needle):
        for sql, params in self.statements:
            if needle in sql:
                return sql, params
        raise AssertionError(f"statement containing {needle!r} not found")


class LoadAnalysisRunConnection:
    def execute(self, sql, params=None):
        if "FROM analysis_runs" in sql:
            return OneRowCursor(
                (
                    1,
                    11,
                    12,
                    "SPY",
                    "0dte",
                    datetime(2026, 7, 28, 0, 30, tzinfo=ZoneInfo("UTC")),
                    "black_scholes_v1",
                    "black_scholes_v1.0.0",
                    "nyse_rules_v2",
                    "option_close_policy_v1",
                    "call_positive_put_negative",
                    0.05,
                    "configured",
                    0.01,
                    "configured",
                    False,
                    ["stored_partial_capture"],
                    3,
                    123456.0,
                    501.25,
                    505.0,
                    495.0,
                )
            )
        if "FROM spot_observations" in sql:
            return OneRowCursor(
                (
                    "SPY",
                    500.0,
                    "test",
                    None,
                    datetime(2026, 7, 28, 0, 30, tzinfo=ZoneInfo("UTC")),
                )
            )
        if "FROM chain_captures" in sql:
            return OneRowCursor(
                (
                    11,
                    "SPY",
                    "test",
                    "success",
                    0,
                    0,
                    [date(2026, 7, 27)],
                    datetime(2026, 7, 27, 15, 55, tzinfo=NY),
                    datetime(2026, 7, 27, 15, 55, tzinfo=NY),
                    [],
                )
            )
        if "FROM expiration_captures" in sql:
            return ManyRowsCursor(
                [
                    (
                        21,
                        date(2026, 7, 27),
                        datetime(2026, 7, 27, 16, 0, tzinfo=NY),
                        datetime(2026, 7, 27, 16, 15, tzinfo=NY),
                        "success",
                        None,
                        None,
                    )
                ]
            )
        if "FROM option_contract_captures" in sql:
            return ManyRowsCursor(
                [
                    (
                        "SPY260727C00500000",
                        500.0,
                        "call",
                        100,
                        10,
                        0.2,
                        None,
                        None,
                        100,
                        1.1,
                        1.2,
                        1.15,
                        1.17,
                        0.5,
                        0.02,
                        -0.03,
                        0.1,
                    )
                ]
            )
        raise AssertionError(f"unexpected SQL: {sql}")


class OneRowCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class ManyRowsCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


if __name__ == "__main__":
    unittest.main()
