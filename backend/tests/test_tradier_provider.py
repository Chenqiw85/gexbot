import unittest
from datetime import date, datetime, timezone

from app.domain.gex import OptionType
from app.providers.base import ProviderDataError
from app.providers.tradier import TradierProvider


class FakeHttpClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get_json(self, path, query, headers=None, **kwargs):
        self.calls.append((path, query, headers))
        return self.payloads.pop(0)


class TradierProviderTest(unittest.TestCase):
    def test_uses_tradier_expiration_endpoint_and_parses_dates(self):
        http = FakeHttpClient([{"expirations": {"date": ["2026-07-27", "2026-07-31"]}}])
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        expirations = provider.available_expirations("spy")

        self.assertEqual(expirations, [date(2026, 7, 27), date(2026, 7, 31)])
        self.assertEqual(http.calls[0][0], "/markets/options/expirations")
        self.assertEqual(http.calls[0][1]["symbol"], "SPY")
        self.assertEqual(http.calls[0][1]["includeAllRoots"], "true")

    def test_dot_class_tickers_use_tradier_slash_symbol_for_market_data_requests(self):
        http = FakeHttpClient(
            [
                {"expirations": {"date": ["2026-07-27"]}},
                {
                    "options": {
                        "option": {
                            "symbol": "BRK.B260727C00500000",
                            "option_type": "call",
                            "strike": 500,
                            "contract_size": 100,
                            "open_interest": 123,
                            "volume": 45,
                            "greeks": {"mid_iv": 0.21},
                        }
                    }
                },
                {"quotes": {"quote": {"symbol": "BRK/B", "last": 500}}},
            ]
        )
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        provider.available_expirations("brk.b")
        provider.option_chain_for_expiration("BRK.B", date(2026, 7, 27))
        quote = provider.spot_quote("BRK.B")

        self.assertEqual(http.calls[0][1]["symbol"], "BRK/B")
        self.assertEqual(http.calls[1][1]["symbol"], "BRK/B")
        self.assertEqual(http.calls[2][1]["symbols"], "BRK/B")
        self.assertEqual(quote.ticker, "BRK.B")

    def test_invalid_expiration_date_raises_provider_data_error(self):
        http = FakeHttpClient([{"expirations": {"date": ["not-a-date"]}}])
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        with self.assertRaises(ProviderDataError) as raised:
            provider.available_expirations("SPY")

        self.assertIn("expiration date", str(raised.exception))
        self.assertIn("not-a-date", str(raised.exception))

    def test_malformed_expiration_payload_raises_provider_data_error(self):
        http = FakeHttpClient([{"expirations": None}])
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        with self.assertRaises(ProviderDataError) as raised:
            provider.available_expirations("SPY")

        self.assertIn("expirations", str(raised.exception))

    def test_parses_chain_without_fabricating_iv_or_oi_times(self):
        http = FakeHttpClient(
            [
                {
                    "options": {
                        "option": {
                            "symbol": "SPY260727C00500000",
                            "option_type": "call",
                            "strike": 500,
                            "contract_size": 100,
                            "open_interest": 123,
                            "volume": 45,
                            "bid": 1.0,
                            "ask": 1.2,
                            "last": 1.1,
                            "greeks": {
                                "mid_iv": 0.21,
                                "delta": 0.5,
                                "gamma": 0.02,
                                "theta": -0.03,
                                "vega": 0.1,
                            },
                        }
                    }
                }
            ]
        )
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        contracts = provider.option_chain_for_expiration("SPY", date(2026, 7, 27))

        self.assertEqual(len(contracts), 1)
        contract = contracts[0]
        self.assertEqual(contract.option_type, OptionType.CALL)
        self.assertEqual(contract.open_interest, 123)
        self.assertEqual(contract.contract_multiplier, 100)
        self.assertEqual(contract.implied_volatility, 0.21)
        self.assertIsNone(contract.iv_observed_at)
        self.assertIsNone(contract.oi_observed_date)

    def test_missing_contract_multiplier_is_not_fabricated(self):
        http = FakeHttpClient(
            [
                {
                    "options": {
                        "option": {
                            "symbol": "SPY260727C00500000",
                            "option_type": "call",
                            "strike": 500,
                            "open_interest": 123,
                            "volume": 45,
                            "greeks": {"mid_iv": 0.21},
                        }
                    }
                }
            ]
        )
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        contracts = provider.option_chain_for_expiration("SPY", date(2026, 7, 27))

        self.assertIsNone(contracts[0].contract_multiplier)

    def test_zero_mid_iv_is_preserved_instead_of_falling_back_or_becoming_missing(self):
        http = FakeHttpClient(
            [
                {
                    "options": {
                        "option": {
                            "symbol": "SPY260727C00500000",
                            "option_type": "call",
                            "strike": 500,
                            "contract_size": 100,
                            "open_interest": 123,
                            "volume": 45,
                            "greeks": {"mid_iv": 0, "smv_vol": 0.21},
                        }
                    }
                }
            ]
        )
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        contracts = provider.option_chain_for_expiration("SPY", date(2026, 7, 27))

        self.assertEqual(contracts[0].implied_volatility, 0.0)

    def test_malformed_optional_chain_numeric_raises_provider_data_error(self):
        http = FakeHttpClient(
            [
                {
                    "options": {
                        "option": {
                            "symbol": "SPY260727C00500000",
                            "option_type": "call",
                            "strike": 500,
                            "contract_size": 100,
                            "open_interest": "bad-oi",
                            "volume": 45,
                            "greeks": {"mid_iv": 0.21},
                        }
                    }
                }
            ]
        )
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        with self.assertRaises(ProviderDataError) as raised:
            provider.option_chain_for_expiration("SPY", date(2026, 7, 27))

        self.assertIn("open_interest", str(raised.exception))
        self.assertIn("bad-oi", str(raised.exception))

    def test_malformed_chain_options_payload_raises_provider_data_error(self):
        http = FakeHttpClient([{"options": None}])
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        with self.assertRaises(ProviderDataError) as raised:
            provider.option_chain_for_expiration("SPY", date(2026, 7, 27))

        self.assertIn("options", str(raised.exception))

    def test_malformed_chain_option_item_raises_provider_data_error(self):
        http = FakeHttpClient([{"options": {"option": [None]}}])
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        with self.assertRaises(ProviderDataError) as raised:
            provider.option_chain_for_expiration("SPY", date(2026, 7, 27))

        self.assertIn("option", str(raised.exception))
        self.assertIn("malformed", str(raised.exception))

    def test_malformed_greeks_payload_raises_provider_data_error(self):
        http = FakeHttpClient(
            [
                {
                    "options": {
                        "option": {
                            "symbol": "SPY260727C00500000",
                            "option_type": "call",
                            "strike": 500,
                            "contract_size": 100,
                            "open_interest": 123,
                            "volume": 45,
                            "greeks": "bad-greeks",
                        }
                    }
                }
            ]
        )
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        with self.assertRaises(ProviderDataError) as raised:
            provider.option_chain_for_expiration("SPY", date(2026, 7, 27))

        self.assertIn("greeks", str(raised.exception))
        self.assertIn("malformed", str(raised.exception))

    def test_unknown_option_type_raises_provider_data_error(self):
        http = FakeHttpClient(
            [
                {
                    "options": {
                        "option": {
                            "symbol": "SPY260727X00500000",
                            "option_type": "straddle",
                            "strike": 500,
                            "contract_size": 100,
                            "open_interest": 123,
                            "volume": 45,
                            "greeks": {"mid_iv": 0.21},
                        }
                    }
                }
            ]
        )
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        with self.assertRaises(ProviderDataError) as raised:
            provider.option_chain_for_expiration("SPY", date(2026, 7, 27))

        self.assertIn("option_type", str(raised.exception))
        self.assertIn("SPY260727X00500000", str(raised.exception))

    def test_missing_option_symbol_raises_provider_data_error(self):
        http = FakeHttpClient(
            [
                {
                    "options": {
                        "option": {
                            "option_type": "call",
                            "strike": 500,
                            "contract_size": 100,
                            "open_interest": 123,
                            "volume": 45,
                            "greeks": {"mid_iv": 0.21},
                        }
                    }
                }
            ]
        )
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        with self.assertRaises(ProviderDataError) as raised:
            provider.option_chain_for_expiration("SPY", date(2026, 7, 27))

        self.assertIn("symbol", str(raised.exception))
        self.assertIn("2026-07-27", str(raised.exception))

    def test_missing_strike_raises_provider_data_error(self):
        http = FakeHttpClient(
            [
                {
                    "options": {
                        "option": {
                            "symbol": "SPY260727C00500000",
                            "option_type": "call",
                            "contract_size": 100,
                            "open_interest": 123,
                            "volume": 45,
                            "greeks": {"mid_iv": 0.21},
                        }
                    }
                }
            ]
        )
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        with self.assertRaises(ProviderDataError) as raised:
            provider.option_chain_for_expiration("SPY", date(2026, 7, 27))

        self.assertIn("strike", str(raised.exception))
        self.assertIn("SPY260727C00500000", str(raised.exception))

    def test_invalid_strike_raises_provider_data_error(self):
        http = FakeHttpClient(
            [
                {
                    "options": {
                        "option": {
                            "symbol": "SPY260727C00500000",
                            "option_type": "call",
                            "strike": "not-a-number",
                            "contract_size": 100,
                            "open_interest": 123,
                            "volume": 45,
                            "greeks": {"mid_iv": 0.21},
                        }
                    }
                }
            ]
        )
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        with self.assertRaises(ProviderDataError) as raised:
            provider.option_chain_for_expiration("SPY", date(2026, 7, 27))

        self.assertIn("strike", str(raised.exception))
        self.assertIn("not numeric", str(raised.exception))

    def test_spot_quote_requires_price_instead_of_silently_using_bad_payload(self):
        http = FakeHttpClient([{"quotes": {"quote": {"symbol": "SPY"}}}])
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        with self.assertRaises(ProviderDataError):
            provider.spot_quote("SPY")

    def test_spot_quote_rejects_non_numeric_price_as_provider_data_error(self):
        http = FakeHttpClient([{"quotes": {"quote": {"symbol": "SPY", "last": "bad-price"}}}])
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        with self.assertRaises(ProviderDataError) as raised:
            provider.spot_quote("SPY")

        self.assertIn("price", str(raised.exception))
        self.assertIn("not numeric", str(raised.exception))

    def test_spot_quote_rejects_non_positive_price_as_provider_data_error(self):
        http = FakeHttpClient([{"quotes": {"quote": {"symbol": "SPY", "last": 0}}}])
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        with self.assertRaises(ProviderDataError) as raised:
            provider.spot_quote("SPY")

        self.assertIn("price", str(raised.exception))
        self.assertIn("positive", str(raised.exception))

    def test_malformed_quote_payload_raises_provider_data_error(self):
        http = FakeHttpClient([{"quotes": None}])
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        with self.assertRaises(ProviderDataError) as raised:
            provider.spot_quote("SPY")

        self.assertIn("quote", str(raised.exception))
        self.assertIn("malformed", str(raised.exception))

    def test_spot_quote_bypasses_http_cache_so_intraday_price_can_change(self):
        http = FakeHttpClient(
            [
                {"quotes": {"quote": {"symbol": "SPY", "last": 500}}},
                {"quotes": {"quote": {"symbol": "SPY", "last": 501}}},
            ]
        )
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        first = provider.spot_quote("SPY")
        second = provider.spot_quote("SPY")

        self.assertEqual(first.spot_price, 500)
        self.assertEqual(second.spot_price, 501)
        self.assertEqual(len(http.calls), 2)

    def test_spot_quote_uses_tradier_trade_date_as_observed_time(self):
        http = FakeHttpClient(
            [
                {
                    "quotes": {
                        "quote": {
                            "symbol": "SPY",
                            "last": 500,
                            "trade_date": 1757948508561,
                        }
                    }
                }
            ]
        )
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        quote = provider.spot_quote("SPY")

        self.assertEqual(
            quote.spot_observed_at,
            datetime.fromtimestamp(1757948508561 / 1000, tz=timezone.utc),
        )

    def test_spot_quote_close_fallback_does_not_parse_trade_date_as_observed_time(self):
        http = FakeHttpClient(
            [
                {
                    "quotes": {
                        "quote": {
                            "symbol": "SPY",
                            "last": None,
                            "close": 499.25,
                            "trade_date": "not-a-timestamp",
                        }
                    }
                }
            ]
        )
        provider = TradierProvider(base_url="https://api.tradier.com/v1", token="token", http_client=http)

        quote = provider.spot_quote("SPY")

        self.assertEqual(quote.spot_price, 499.25)
        self.assertIsNone(quote.spot_observed_at)


if __name__ == "__main__":
    unittest.main()
