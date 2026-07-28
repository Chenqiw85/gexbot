import unittest
import io
import json
from contextlib import redirect_stderr
from datetime import datetime
from dataclasses import replace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.providers.mock import MockMarketDataProvider


NY = ZoneInfo("America/New_York")


class ProviderSmokeTest(unittest.TestCase):
    def test_provider_smoke_collects_spot_expiration_and_chain_summary(self):
        try:
            from app.providers.smoke import run_provider_smoke
        except ModuleNotFoundError:
            self.fail("run_provider_smoke is missing")

        provider = MockMarketDataProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))

        result = run_provider_smoke(provider, ticker="spy", max_expirations=2)

        self.assertEqual(result.ticker, "SPY")
        self.assertEqual(result.source, "mock")
        self.assertEqual(result.expiration_count, 2)
        self.assertEqual(result.contract_count, 20)
        self.assertEqual(result.spot_price, 500.0)
        self.assertTrue(result.has_iv)
        self.assertTrue(result.has_open_interest)
        self.assertTrue(result.has_contract_multiplier)
        self.assertEqual(result.warnings, ())

    def test_smoke_main_fails_when_required_gex_inputs_are_missing(self):
        from app.providers.smoke import main

        provider = MissingRequiredFieldProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))

        stderr = io.StringIO()
        with patch("app.providers.smoke.make_provider", return_value=provider), redirect_stderr(stderr):
            exit_code = main(["--ticker", "SPY", "--max-expirations", "1"])

        payload = json.loads(stderr.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["has_iv"])
        self.assertFalse(payload["has_open_interest"])
        self.assertFalse(payload["has_contract_multiplier"])
        self.assertIn("missing required GEX inputs", payload["error"])

    def test_provider_smoke_validates_ticker_before_provider_calls(self):
        from app.providers.smoke import run_provider_smoke

        provider = CountingProvider(now=datetime(2026, 7, 27, 10, 0, tzinfo=NY))

        with self.assertRaises(ValueError) as raised:
            run_provider_smoke(provider, ticker="BAD TICKER", max_expirations=1)

        self.assertIn("ticker must be 1-12 chars", str(raised.exception))
        self.assertEqual(provider.available_expiration_calls, 0)
        self.assertEqual(provider.option_chain_calls, 0)
        self.assertEqual(provider.spot_quote_calls, 0)


class MissingRequiredFieldProvider(MockMarketDataProvider):
    def option_chain_for_expiration(self, ticker, expiration):
        return [
            replace(
                contract,
                implied_volatility=None,
                open_interest=None,
                contract_multiplier=None,
            )
            for contract in super().option_chain_for_expiration(ticker, expiration)
        ]


class CountingProvider(MockMarketDataProvider):
    def __init__(self, *, now):
        super().__init__(now=now)
        self.available_expiration_calls = 0
        self.option_chain_calls = 0
        self.spot_quote_calls = 0

    def available_expirations(self, ticker):
        self.available_expiration_calls += 1
        return super().available_expirations(ticker)

    def option_chain_for_expiration(self, ticker, expiration):
        self.option_chain_calls += 1
        return super().option_chain_for_expiration(ticker, expiration)

    def spot_quote(self, ticker):
        self.spot_quote_calls += 1
        return super().spot_quote(ticker)


if __name__ == "__main__":
    unittest.main()
