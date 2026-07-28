import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.domain.calendar import OptionClosePolicy, build_expiration_times
from app.domain.gex import (
    AnalysisInput,
    ContractSnapshot,
    OptionType,
    PositionAssumption,
    analyze_gex_proxy,
)


NY = ZoneInfo("America/New_York")


def contract(
    *,
    option_type: OptionType,
    strike: float,
    open_interest: int,
    iv: float | None = 0.20,
    multiplier: int | None = 100,
    expiration_date: date = date(2026, 7, 17),
):
    times = build_expiration_times("SPY", expiration_date, OptionClosePolicy.late_option_1615())
    return ContractSnapshot(
        option_symbol=f"SPY{expiration_date:%y%m%d}{option_type.value[0].upper()}{int(strike * 1000):08d}",
        strike=strike,
        option_type=option_type,
        open_interest=open_interest,
        volume=10,
        implied_volatility=iv,
        iv_observed_at=None,
        oi_observed_date=None,
        contract_multiplier=multiplier,
        underlying_close_at=times.underlying_close_at,
        option_last_trade_at=times.option_last_trade_at,
    )


class GexProxyTest(unittest.TestCase):
    def test_call_positive_put_negative_sign_convention_and_multiplier(self):
        analyzed_at = datetime(2026, 7, 17, 10, 0, tzinfo=NY)
        base = AnalysisInput(
            ticker="SPY",
            scope="all",
            spot_price=500.0,
            spot_observed_at=analyzed_at,
            spot_provider_received_at=analyzed_at,
            analyzed_at=analyzed_at,
            risk_free_rate=0.05,
            dividend_yield=0.01,
            position_assumption=PositionAssumption.CALL_POSITIVE_PUT_NEGATIVE,
            contracts=[
                contract(option_type=OptionType.CALL, strike=500.0, open_interest=100, multiplier=100),
                contract(option_type=OptionType.PUT, strike=500.0, open_interest=100, multiplier=100),
            ],
        )

        doubled_multiplier = AnalysisInput(
            **{**base.__dict__, "contracts": [
                contract(option_type=OptionType.CALL, strike=500.0, open_interest=100, multiplier=200),
                contract(option_type=OptionType.PUT, strike=500.0, open_interest=100, multiplier=200),
            ]}
        )

        result = analyze_gex_proxy(base)
        doubled = analyze_gex_proxy(doubled_multiplier)

        row = result.rows[500.0]
        self.assertGreater(row.call_gex_proxy, 0)
        self.assertLess(row.put_gex_proxy, 0)
        self.assertAlmostEqual(row.net_gex_proxy, row.call_gex_proxy + row.put_gex_proxy)
        self.assertAlmostEqual(doubled.rows[500.0].call_gex_proxy, row.call_gex_proxy * 2, delta=1e-6)

    def test_expired_contracts_are_excluded_instead_of_clamped(self):
        analyzed_at = datetime(2026, 7, 17, 16, 16, tzinfo=NY)
        result = analyze_gex_proxy(
            AnalysisInput(
                ticker="SPY",
                scope="0dte",
                spot_price=500.0,
                spot_observed_at=analyzed_at,
                spot_provider_received_at=analyzed_at,
                analyzed_at=analyzed_at,
                risk_free_rate=0.05,
                dividend_yield=0.01,
                position_assumption=PositionAssumption.CALL_POSITIVE_PUT_NEGATIVE,
                contracts=[
                    contract(option_type=OptionType.CALL, strike=500.0, open_interest=100),
                    contract(option_type=OptionType.PUT, strike=500.0, open_interest=100),
                ],
            )
        )

        self.assertEqual(result.rows, {})
        self.assertIsNone(result.zero_gamma_proxy)
        self.assertEqual(result.excluded_contract_count, 2)
        self.assertIn("expired_contracts_excluded", result.warnings)

    def test_etf_contracts_remain_live_until_option_last_trade(self):
        analyzed_at = datetime(2026, 7, 17, 16, 5, tzinfo=NY)

        result = analyze_gex_proxy(
            AnalysisInput(
                ticker="SPY",
                scope="0dte",
                spot_price=500.0,
                spot_observed_at=analyzed_at,
                spot_provider_received_at=analyzed_at,
                analyzed_at=analyzed_at,
                risk_free_rate=0.05,
                dividend_yield=0.01,
                position_assumption=PositionAssumption.CALL_POSITIVE_PUT_NEGATIVE,
                contracts=[
                    contract(option_type=OptionType.CALL, strike=500.0, open_interest=100),
                    contract(option_type=OptionType.PUT, strike=500.0, open_interest=50),
                ],
            )
        )

        self.assertIn(500.0, result.rows)
        self.assertEqual(result.excluded_contract_count, 0)
        self.assertNotIn("expired_contracts_excluded", result.warnings)

    def test_missing_iv_and_missing_multiplier_are_not_silently_used(self):
        analyzed_at = datetime(2026, 7, 17, 10, 0, tzinfo=NY)

        result = analyze_gex_proxy(
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
                contracts=[
                    contract(option_type=OptionType.CALL, strike=500.0, open_interest=100, iv=None),
                    contract(option_type=OptionType.PUT, strike=500.0, open_interest=100, multiplier=None),
                ],
            )
        )

        self.assertEqual(result.rows, {})
        self.assertIn("missing_iv_excluded", result.warnings)
        self.assertIn("missing_multiplier_excluded", result.warnings)
        self.assertIn("all_contracts_excluded", result.warnings)

    def test_non_positive_iv_strike_and_multiplier_are_excluded(self):
        analyzed_at = datetime(2026, 7, 17, 10, 0, tzinfo=NY)

        result = analyze_gex_proxy(
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
                contracts=[
                    contract(option_type=OptionType.CALL, strike=500.0, open_interest=100, iv=0),
                    contract(option_type=OptionType.CALL, strike=0.0, open_interest=100),
                    contract(option_type=OptionType.PUT, strike=500.0, open_interest=100, multiplier=0),
                ],
            )
        )

        self.assertEqual(result.rows, {})
        self.assertEqual(result.excluded_contract_count, 3)
        self.assertIn("non_positive_iv_excluded", result.warnings)
        self.assertIn("non_positive_strike_excluded", result.warnings)
        self.assertIn("non_positive_multiplier_excluded", result.warnings)

    def test_wall_definitions_and_nearest_zero_gamma_crossing(self):
        analyzed_at = datetime(2026, 7, 17, 10, 0, tzinfo=NY)
        result = analyze_gex_proxy(
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
                contracts=[
                    contract(option_type=OptionType.CALL, strike=510.0, open_interest=300),
                    contract(option_type=OptionType.CALL, strike=520.0, open_interest=100),
                    contract(option_type=OptionType.PUT, strike=490.0, open_interest=400),
                    contract(option_type=OptionType.PUT, strike=480.0, open_interest=100),
                ],
            )
        )

        self.assertEqual(result.call_wall_proxy, max(result.rows.values(), key=lambda row: row.call_gex_proxy).strike)
        self.assertEqual(result.put_wall_proxy, min(result.rows.values(), key=lambda row: row.put_gex_proxy).strike)
        self.assertFalse(any("flip" in field_name for field_name in result.__dataclass_fields__))
        self.assertTrue(result.zero_gamma_proxy is None or result.zero_gamma_proxy > 0)


if __name__ == "__main__":
    unittest.main()
