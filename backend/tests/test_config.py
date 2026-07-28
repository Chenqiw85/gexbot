import os
import unittest
from unittest.mock import patch

from app.config import get_settings


class SettingsTest(unittest.TestCase):
    def test_provider_name_is_normalized_and_restricted_to_supported_values(self):
        with patch.dict(os.environ, {"GEXBOT_PROVIDER": " Tradier ", "TRADIER_TOKEN": "token"}, clear=True):
            settings = get_settings()

        self.assertEqual(settings.provider, "tradier")

        with patch.dict(os.environ, {"GEXBOT_PROVIDER": "tradreir"}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()

        self.assertIn("GEXBOT_PROVIDER must be one of", str(raised.exception))

    def test_tradier_provider_requires_token_at_configuration_time(self):
        with patch.dict(os.environ, {"GEXBOT_PROVIDER": "tradier"}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()

        self.assertIn("TRADIER_TOKEN is required when GEXBOT_PROVIDER=tradier", str(raised.exception))

        with patch.dict(os.environ, {"GEXBOT_PROVIDER": "tradier", "TRADIER_TOKEN": "   "}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()

        self.assertIn("TRADIER_TOKEN is required when GEXBOT_PROVIDER=tradier", str(raised.exception))

    def test_default_tickers_are_normalized_and_validated_from_environment(self):
        with patch.dict(os.environ, {"GEXBOT_TICKERS": " spy, brk.b "}, clear=True):
            settings = get_settings()

        self.assertEqual(settings.default_tickers, ("SPY", "BRK.B"))

        with patch.dict(os.environ, {"GEXBOT_TICKERS": "SPY,BAD TICKER"}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()

        self.assertIn("ticker must be 1-12 chars", str(raised.exception))

    def test_scheduler_cadence_and_dte_range_are_validated_from_environment(self):
        with patch.dict(os.environ, {"CHAIN_REFRESH_SECONDS": "0"}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()

        self.assertIn("CHAIN_REFRESH_SECONDS must be positive", str(raised.exception))

        with patch.dict(os.environ, {"DEFAULT_MIN_DTE": "10", "DEFAULT_MAX_DTE": "3"}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()

        self.assertIn("DEFAULT_MIN_DTE must be less than or equal to DEFAULT_MAX_DTE", str(raised.exception))

    def test_default_dividend_yield_is_validated_from_environment(self):
        with patch.dict(os.environ, {"DEFAULT_DIVIDEND_YIELD": "-0.001"}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()

        self.assertIn("DEFAULT_DIVIDEND_YIELD must be between 0 and 1", str(raised.exception))

        with patch.dict(os.environ, {"DEFAULT_DIVIDEND_YIELD": "1.001"}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()

        self.assertIn("DEFAULT_DIVIDEND_YIELD must be between 0 and 1", str(raised.exception))

    def test_model_rate_inputs_must_be_finite_numbers(self):
        with patch.dict(os.environ, {"DEFAULT_RISK_FREE_RATE": "nan"}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()

        self.assertIn("DEFAULT_RISK_FREE_RATE must be finite", str(raised.exception))

        with patch.dict(os.environ, {"DEFAULT_RISK_FREE_RATE": "inf"}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()

        self.assertIn("DEFAULT_RISK_FREE_RATE must be finite", str(raised.exception))

        with patch.dict(os.environ, {"DEFAULT_DIVIDEND_YIELD": "nan"}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()

        self.assertIn("DEFAULT_DIVIDEND_YIELD must be finite", str(raised.exception))

        with patch.dict(os.environ, {"DEFAULT_RISK_FREE_RATE": "-0.005"}, clear=True):
            settings = get_settings()

        self.assertEqual(settings.default_risk_free_rate, -0.005)

    def test_boolean_environment_values_are_validated(self):
        with patch.dict(os.environ, {"AUTO_ENSURE_SCHEMA": "maybe"}, clear=True):
            with self.assertRaises(ValueError) as raised:
                get_settings()

        self.assertIn("AUTO_ENSURE_SCHEMA must be a boolean", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
