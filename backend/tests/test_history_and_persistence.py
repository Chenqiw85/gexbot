import unittest
from datetime import date, datetime
from dataclasses import replace
from zoneinfo import ZoneInfo

from app.domain.calendar import OptionClosePolicy, build_expiration_times
from app.domain.gex import AnalysisInput, ContractSnapshot, OptionType, PositionAssumption
from app.services.history import UnsupportedModelVersion, recompute_from_record
from app.services.persistence import InMemorySnapshotStore


NY = ZoneInfo("America/New_York")


class HistoryAndPersistenceTest(unittest.TestCase):
    def _contract(self):
        expiration_date = date(2026, 7, 17)
        times = build_expiration_times("SPY", expiration_date, OptionClosePolicy.late_option_1615())
        return ContractSnapshot(
            option_symbol="SPY260717C00500000",
            strike=500.0,
            option_type=OptionType.CALL,
            open_interest=100,
            volume=12,
            implied_volatility=0.2,
            iv_observed_at=None,
            oi_observed_date=None,
            contract_multiplier=100,
            underlying_close_at=times.underlying_close_at,
            option_last_trade_at=times.option_last_trade_at,
        )

    def test_spot_only_analysis_does_not_persist_strike_rows_by_default(self):
        store = InMemorySnapshotStore()
        analyzed_at = datetime(2026, 7, 17, 10, 0, tzinfo=NY)

        run_id = store.save_analysis_run(
            AnalysisInput(
                ticker="SPY",
                scope="all",
                spot_price=500.0,
                spot_observed_at=analyzed_at,
                spot_provider_received_at=analyzed_at,
                analyzed_at=analyzed_at,
                risk_free_rate=0.05,
                dividend_yield=0.01,
                position_assumption=PositionAssumption.CALL_POSITIVE_PUT_NEGATIVE,
                contracts=[self._contract()],
            ),
            persist_strike_rows=False,
        )

        self.assertEqual(len(store.analysis_runs), 1)
        self.assertEqual(store.analysis_runs[run_id].persist_strike_rows, False)
        self.assertEqual(store.strike_rows_by_run.get(run_id), None)

    def test_explicit_persist_rows_checkpoint_saves_rows_for_replay(self):
        store = InMemorySnapshotStore()
        analyzed_at = datetime(2026, 7, 17, 10, 0, tzinfo=NY)

        run_id = store.save_analysis_run(
            AnalysisInput(
                ticker="SPY",
                scope="all",
                spot_price=500.0,
                spot_observed_at=analyzed_at,
                spot_provider_received_at=analyzed_at,
                analyzed_at=analyzed_at,
                risk_free_rate=0.05,
                dividend_yield=0.01,
                position_assumption=PositionAssumption.CALL_POSITIVE_PUT_NEGATIVE,
                contracts=[self._contract()],
            ),
            persist_strike_rows=True,
        )

        self.assertIn(run_id, store.strike_rows_by_run)
        self.assertEqual(len(store.strike_rows_by_run[run_id]), 1)

    def test_historical_recompute_uses_stored_inputs(self):
        store = InMemorySnapshotStore()
        analyzed_at = datetime(2026, 7, 17, 10, 0, tzinfo=NY)
        run_id = store.save_analysis_run(
            AnalysisInput(
                ticker="SPY",
                scope="all",
                spot_price=500.0,
                spot_observed_at=analyzed_at,
                spot_provider_received_at=analyzed_at,
                analyzed_at=analyzed_at,
                risk_free_rate=0.05,
                dividend_yield=0.01,
                position_assumption=PositionAssumption.CALL_POSITIVE_PUT_NEGATIVE,
                contracts=[self._contract()],
            ),
            persist_strike_rows=False,
        )

        original = store.analysis_runs[run_id]
        recomputed = recompute_from_record(original)

        self.assertEqual(recomputed.model_inputs.model_version, original.model_inputs.model_version)
        self.assertEqual(recomputed.model_inputs.calendar_version, original.model_inputs.calendar_version)
        self.assertEqual(recomputed.model_inputs.option_close_policy_version, original.model_inputs.option_close_policy_version)
        self.assertAlmostEqual(recomputed.net_gex_proxy, original.net_gex_proxy)

    def test_historical_recompute_preserves_spot_observed_and_received_times(self):
        store = InMemorySnapshotStore()
        analyzed_at = datetime(2026, 7, 17, 10, 0, tzinfo=NY)
        provider_received_at = datetime(2026, 7, 17, 10, 0, 2, tzinfo=NY)

        run_id = store.save_analysis_run(
            AnalysisInput(
                ticker="SPY",
                scope="all",
                spot_price=500.0,
                spot_observed_at=None,
                spot_provider_received_at=provider_received_at,
                analyzed_at=analyzed_at,
                risk_free_rate=0.05,
                dividend_yield=0.01,
                position_assumption=PositionAssumption.CALL_POSITIVE_PUT_NEGATIVE,
                contracts=[self._contract()],
            ),
            persist_strike_rows=False,
        )

        original = store.analysis_runs[run_id]
        recomputed = recompute_from_record(original)

        self.assertIsNone(recomputed.model_inputs.spot_observed_at)
        self.assertEqual(recomputed.model_inputs.spot_provider_received_at, provider_received_at)

    def test_historical_recompute_rejects_unknown_model_version(self):
        store = InMemorySnapshotStore()
        analyzed_at = datetime(2026, 7, 17, 10, 0, tzinfo=NY)
        run_id = store.save_analysis_run(
            AnalysisInput(
                ticker="SPY",
                scope="all",
                spot_price=500.0,
                spot_observed_at=analyzed_at,
                spot_provider_received_at=analyzed_at,
                analyzed_at=analyzed_at,
                risk_free_rate=0.05,
                dividend_yield=0.01,
                position_assumption=PositionAssumption.CALL_POSITIVE_PUT_NEGATIVE,
                contracts=[self._contract()],
            ),
            persist_strike_rows=False,
        )
        original = store.analysis_runs[run_id]
        unsupported = replace(
            original,
            input_data=replace(original.input_data, model_version="black_scholes_v2.0.0"),
        )

        with self.assertRaises(UnsupportedModelVersion) as raised:
            recompute_from_record(unsupported)

        self.assertIn("black_scholes_v2.0.0", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
