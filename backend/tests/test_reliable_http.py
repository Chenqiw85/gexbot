import unittest

from app.providers.http import (
    HttpRequestError,
    HttpResponse,
    MemoryResponseCache,
    RateLimitExceeded,
    ReliableHttpClient,
)


class FakeTransport:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, path, query, headers, timeout):
        self.calls.append((path, query, headers, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ReliableHttpClientTest(unittest.TestCase):
    def test_retries_transient_failures_and_caches_successful_json_get(self):
        transport = FakeTransport(
            [
                TimeoutError("first timeout"),
                TimeoutError("second timeout"),
                HttpResponse(status_code=200, headers={}, body=b'{"ok": true}'),
            ]
        )
        client = ReliableHttpClient(
            transport=transport,
            cache=MemoryResponseCache(),
            max_attempts=3,
            backoff_seconds=0,
        )

        first = client.get_json("/markets/quotes", {"symbols": "SPY"})
        second = client.get_json("/markets/quotes", {"symbols": "SPY"})

        self.assertEqual(first, {"ok": True})
        self.assertEqual(second, {"ok": True})
        self.assertEqual(len(transport.calls), 3)

    def test_raises_rate_limit_error_on_429_without_retrying(self):
        transport = FakeTransport(
            [
                HttpResponse(
                    status_code=429,
                    headers={"X-Ratelimit-Available": "0", "X-Ratelimit-Expiry": "30"},
                    body=b'{"errors": {"error": "rate limit"}}',
                )
            ]
        )
        client = ReliableHttpClient(
            transport=transport,
            cache=MemoryResponseCache(),
            max_attempts=3,
            backoff_seconds=0,
        )

        with self.assertRaises(RateLimitExceeded) as raised:
            client.get_json("/markets/options/chains", {"symbol": "SPY"})

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.rate_limit_available, "0")
        self.assertEqual(len(transport.calls), 1)

    def test_rate_limit_headers_are_case_insensitive(self):
        transport = FakeTransport(
            [
                HttpResponse(
                    status_code=429,
                    headers={"x-ratelimit-available": "0", "x-ratelimit-expiry": "30"},
                    body=b'{"errors": {"error": "rate limit"}}',
                )
            ]
        )
        client = ReliableHttpClient(
            transport=transport,
            cache=MemoryResponseCache(),
            max_attempts=1,
            backoff_seconds=0,
        )

        with self.assertRaises(RateLimitExceeded) as raised:
            client.get_json("/markets/options/chains", {"symbol": "SPY"})

        self.assertEqual(raised.exception.rate_limit_available, "0")
        self.assertEqual(raised.exception.rate_limit_expiry, "30")

    def test_non_success_response_raises_structured_error(self):
        transport = FakeTransport(
            [
                HttpResponse(status_code=400, headers={}, body=b'{"errors": {"error": "bad symbol"}}'),
            ]
        )
        client = ReliableHttpClient(
            transport=transport,
            cache=MemoryResponseCache(),
            max_attempts=1,
            backoff_seconds=0,
        )

        with self.assertRaises(HttpRequestError) as raised:
            client.get_json("/markets/options/expirations", {"symbol": "NOPE"})

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("/markets/options/expirations", str(raised.exception))

    def test_raises_last_5xx_status_after_retry_exhaustion(self):
        transport = FakeTransport(
            [
                HttpResponse(status_code=503, headers={}, body=b'{"errors": {"error": "maintenance"}}'),
                HttpResponse(status_code=503, headers={}, body=b'{"errors": {"error": "still down"}}'),
            ]
        )
        client = ReliableHttpClient(
            transport=transport,
            cache=MemoryResponseCache(),
            max_attempts=2,
            backoff_seconds=0,
        )

        with self.assertRaises(HttpRequestError) as raised:
            client.get_json("/markets/options/chains", {"symbol": "SPY"})

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("still down", raised.exception.message)
        self.assertEqual(len(transport.calls), 2)

    def test_cache_can_be_bypassed_per_request(self):
        transport = FakeTransport(
            [
                HttpResponse(status_code=200, headers={}, body=b'{"price": 500}'),
                HttpResponse(status_code=200, headers={}, body=b'{"price": 501}'),
            ]
        )
        client = ReliableHttpClient(
            transport=transport,
            cache=MemoryResponseCache(),
            max_attempts=1,
            backoff_seconds=0,
        )

        first = client.get_json("/markets/quotes", {"symbols": "SPY"}, use_cache=False)
        second = client.get_json("/markets/quotes", {"symbols": "SPY"}, use_cache=False)

        self.assertEqual(first["price"], 500)
        self.assertEqual(second["price"], 501)
        self.assertEqual(len(transport.calls), 2)

    def test_cache_entries_expire_after_configured_ttl(self):
        clock = {"now": 100.0}

        def now():
            return clock["now"]

        transport = FakeTransport(
            [
                HttpResponse(status_code=200, headers={}, body=b'{"price": 500}'),
                HttpResponse(status_code=200, headers={}, body=b'{"price": 501}'),
            ]
        )
        cache = MemoryResponseCache()
        cache.ttl_seconds = 10
        cache.time_fn = now
        client = ReliableHttpClient(
            transport=transport,
            cache=cache,
            max_attempts=1,
            backoff_seconds=0,
        )

        first = client.get_json("/markets/options/chains", {"symbol": "SPY"})
        clock["now"] = 109.0
        cached = client.get_json("/markets/options/chains", {"symbol": "SPY"})
        clock["now"] = 111.0
        refreshed = client.get_json("/markets/options/chains", {"symbol": "SPY"})

        self.assertEqual(first["price"], 500)
        self.assertEqual(cached["price"], 500)
        self.assertEqual(refreshed["price"], 501)
        self.assertEqual(len(transport.calls), 2)

    def test_cached_json_is_isolated_from_caller_mutation(self):
        transport = FakeTransport(
            [
                HttpResponse(status_code=200, headers={}, body=b'{"chain": {"count": 30}}'),
            ]
        )
        client = ReliableHttpClient(
            transport=transport,
            cache=MemoryResponseCache(),
            max_attempts=1,
            backoff_seconds=0,
        )

        first = client.get_json("/markets/options/chains", {"symbol": "SPY"})
        first["chain"]["count"] = 0
        second = client.get_json("/markets/options/chains", {"symbol": "SPY"})

        self.assertEqual(second, {"chain": {"count": 30}})
        self.assertEqual(len(transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
