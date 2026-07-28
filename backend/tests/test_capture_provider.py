import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.domain.gex import OptionType
from app.providers.base import MarketDataProvider, ProviderOptionContract, SpotQuote
from app.providers.mock import MockMarketDataProvider
from app.services.capture import ChainCaptureRequest, capture_option_chain


NY = ZoneInfo("America/New_York")


class CaptureProviderTest(unittest.TestCase):
    def test_expiration_range_limits_requested_contracts(self):
        provider = MockMarketDataProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))

        capture = capture_option_chain(
            provider,
            ChainCaptureRequest(ticker="SPY", min_dte=0, max_dte=7, expirations=None),
        )

        expirations = [item.expiration_date for item in capture.expiration_captures]
        self.assertEqual(expirations, [date(2026, 7, 27), date(2026, 7, 31)])
        self.assertEqual(capture.status, "success")

    def test_rejects_inverted_effective_expiration_range(self):
        provider = MockMarketDataProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))

        with self.assertRaises(ValueError) as raised:
            capture_option_chain(
                provider,
                ChainCaptureRequest(ticker="SPY", min_dte=60, max_dte=45, expirations=None),
            )

        self.assertIn("min_dte", str(raised.exception))

    def test_expiration_range_uses_capture_date_not_first_available_expiration(self):
        provider = ProviderWithFutureExpirations(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))

        capture = capture_option_chain(
            provider,
            ChainCaptureRequest(ticker="SPY", min_dte=0, max_dte=0, expirations=None),
        )

        self.assertEqual(capture.status, "failed")
        self.assertEqual(capture.requested_expirations, [])
        self.assertEqual(capture.expiration_captures, [])
        self.assertIn("no_expirations_selected", capture.warnings)

    def test_expiration_range_uses_new_york_market_date_for_utc_provider_time(self):
        provider = ProviderWithExplicitExpirations(
            now=datetime(2026, 7, 28, 0, 30, tzinfo=timezone.utc),
            expirations=[date(2026, 7, 27)],
        )

        capture = capture_option_chain(
            provider,
            ChainCaptureRequest(ticker="SPY", min_dte=0, max_dte=0, expirations=None),
        )

        self.assertEqual(capture.requested_expirations, [date(2026, 7, 27)])

    def test_explicit_expirations_are_still_limited_by_dte_range(self):
        provider = ProviderWithExplicitExpirations(
            now=datetime(2026, 7, 27, 10, 0, tzinfo=NY),
            expirations=[date(2026, 7, 27), date(2026, 9, 18)],
        )

        capture = capture_option_chain(
            provider,
            ChainCaptureRequest(
                ticker="SPY",
                min_dte=0,
                max_dte=7,
                expirations=[date(2026, 7, 27), date(2026, 9, 18)],
            ),
        )

        self.assertEqual(capture.requested_expirations, [date(2026, 7, 27)])
        self.assertEqual([item.expiration_date for item in capture.expiration_captures], [date(2026, 7, 27)])

    def test_partial_capture_keeps_successful_expirations_and_records_failures(self):
        provider = MockMarketDataProvider(
            now=datetime(2026, 7, 27, 10, 0, tzinfo=NY),
            failing_expirations={date(2026, 7, 31)},
        )

        capture = capture_option_chain(
            provider,
            ChainCaptureRequest(ticker="SPY", min_dte=0, max_dte=10, expirations=None),
        )

        statuses = {item.expiration_date: item.status for item in capture.expiration_captures}
        self.assertEqual(capture.status, "partial")
        self.assertEqual(statuses[date(2026, 7, 27)], "success")
        self.assertEqual(statuses[date(2026, 7, 31)], "failed")
        self.assertIn("partial_capture", capture.warnings)

    def test_empty_expiration_chain_is_recorded_as_partial_capture_failure(self):
        provider = ProviderWithEmptyExpiration(
            now=datetime(2026, 7, 27, 10, 0, tzinfo=NY),
            empty_expiration=date(2026, 7, 27),
        )

        capture = capture_option_chain(
            provider,
            ChainCaptureRequest(ticker="SPY", min_dte=0, max_dte=7, expirations=None),
        )

        statuses = {item.expiration_date: item.status for item in capture.expiration_captures}
        failures = {item.expiration_date: item.error_message for item in capture.expiration_captures}
        self.assertEqual(capture.status, "partial")
        self.assertEqual(statuses[date(2026, 7, 27)], "failed")
        self.assertEqual(statuses[date(2026, 7, 31)], "success")
        self.assertIn("no option contracts", failures[date(2026, 7, 27)])
        self.assertIn("partial_capture", capture.warnings)

    def test_unknown_iv_and_oi_times_remain_null(self):
        provider = MockMarketDataProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))

        capture = capture_option_chain(
            provider,
            ChainCaptureRequest(ticker="SPY", min_dte=0, max_dte=0, expirations=None),
        )

        first_contract = capture.expiration_captures[0].contracts[0]
        self.assertIsNone(first_contract.iv_observed_at)
        self.assertIsNone(first_contract.oi_observed_date)
        self.assertIsNotNone(capture.provider_received_at)

    def test_capture_preserves_provider_greeks_and_quote_fields(self):
        provider = ProviderWithGreeksAndQuotes()

        capture = capture_option_chain(
            provider,
            ChainCaptureRequest(ticker="SPY", min_dte=0, max_dte=0, expirations=None),
        )

        first_contract = capture.expiration_captures[0].contracts[0]
        self.assertEqual(getattr(first_contract, "bid", None), 1.1)
        self.assertEqual(getattr(first_contract, "ask", None), 1.2)
        self.assertEqual(getattr(first_contract, "last", None), 1.15)
        self.assertEqual(getattr(first_contract, "mark", None), 1.17)
        self.assertEqual(getattr(first_contract, "vendor_delta", None), 0.51)
        self.assertEqual(getattr(first_contract, "vendor_gamma", None), 0.02)
        self.assertEqual(getattr(first_contract, "vendor_theta", None), -0.03)
        self.assertEqual(getattr(first_contract, "vendor_vega", None), 0.12)


class ProviderWithGreeksAndQuotes(MarketDataProvider):
    source = "test"
    now = datetime(2026, 7, 27, 10, 0, tzinfo=NY)

    def available_expirations(self, ticker):
        return [date(2026, 7, 27)]

    def option_chain_for_expiration(self, ticker, expiration):
        return [
            ProviderOptionContract(
                option_symbol="SPY260727C00500000",
                expiration_date=expiration,
                strike=500.0,
                option_type=OptionType.CALL,
                open_interest=100,
                volume=10,
                implied_volatility=0.2,
                iv_observed_at=None,
                oi_observed_date=None,
                contract_multiplier=100,
                bid=1.1,
                ask=1.2,
                last=1.15,
                mark=1.17,
                vendor_delta=0.51,
                vendor_gamma=0.02,
                vendor_theta=-0.03,
                vendor_vega=0.12,
            )
        ]

    def spot_quote(self, ticker):
        return SpotQuote(ticker=ticker, spot_price=500.0, spot_observed_at=None, provider_received_at=self.now)


class ProviderWithEmptyExpiration(MockMarketDataProvider):
    def __init__(self, *, now, empty_expiration):
        super().__init__(now=now)
        self.empty_expiration = empty_expiration

    def option_chain_for_expiration(self, ticker, expiration):
        if expiration == self.empty_expiration:
            return []
        return super().option_chain_for_expiration(ticker, expiration)


class ProviderWithFutureExpirations(MarketDataProvider):
    source = "test"

    def __init__(self, *, now):
        self.now = now

    def available_expirations(self, ticker):
        return [date(2026, 7, 31), date(2026, 8, 7)]

    def option_chain_for_expiration(self, ticker, expiration):
        return []

    def spot_quote(self, ticker):
        return SpotQuote(ticker=ticker, spot_price=500.0, spot_observed_at=None, provider_received_at=self.now)


class ProviderWithExplicitExpirations(MarketDataProvider):
    source = "test"

    def __init__(self, *, now, expirations):
        self.now = now
        self.expirations = expirations

    def available_expirations(self, ticker):
        return self.expirations

    def option_chain_for_expiration(self, ticker, expiration):
        return []

    def spot_quote(self, ticker):
        return SpotQuote(ticker=ticker, spot_price=500.0, spot_observed_at=None, provider_received_at=self.now)


if __name__ == "__main__":
    unittest.main()
