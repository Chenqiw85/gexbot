from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


class HttpRequestError(RuntimeError):
    def __init__(self, *, path: str, status_code: int | None, message: str) -> None:
        self.path = path
        self.status_code = status_code
        self.message = message
        super().__init__(f"{path} failed status={status_code}: {message}")


class RateLimitExceeded(HttpRequestError):
    def __init__(self, *, path: str, status_code: int, headers: dict[str, str], message: str) -> None:
        self.rate_limit_available = _header(headers, "X-Ratelimit-Available")
        self.rate_limit_expiry = _header(headers, "X-Ratelimit-Expiry")
        super().__init__(path=path, status_code=status_code, message=message)


class MemoryResponseCache:
    def __init__(self, *, ttl_seconds: float | None = None, time_fn: Callable[[], float] = time.monotonic) -> None:
        self.ttl_seconds = ttl_seconds
        self.time_fn = time_fn
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, Any]] = {}

    def get(self, path: str, query: dict[str, str]) -> Any | None:
        key = _cache_key(path, query)
        item = self._values.get(key)
        if item is None:
            return None
        stored_at, value = item
        if self.ttl_seconds is not None and self.time_fn() - stored_at >= self.ttl_seconds:
            del self._values[key]
            return None
        return copy.deepcopy(value)

    def set(self, path: str, query: dict[str, str], value: Any) -> None:
        self._values[_cache_key(path, query)] = (self.time_fn(), copy.deepcopy(value))


Transport = Callable[[str, dict[str, str], dict[str, str], float], HttpResponse]


class ReliableHttpClient:
    def __init__(
        self,
        *,
        transport: Transport,
        cache: MemoryResponseCache | None = None,
        max_attempts: int = 3,
        backoff_seconds: float = 0.25,
        timeout_seconds: float = 30,
    ) -> None:
        self.transport = transport
        self.cache = cache or MemoryResponseCache()
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.timeout_seconds = timeout_seconds

    def get_json(
        self,
        path: str,
        query: dict[str, str],
        headers: dict[str, str] | None = None,
        *,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        cached = self.cache.get(path, query) if use_cache else None
        if use_cache and cached is not None:
            return cached

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.transport(path, query, headers or {}, self.timeout_seconds)
                value = _json_or_error(path, response)
                if use_cache:
                    self.cache.set(path, query, value)
                return value
            except RateLimitExceeded:
                raise
            except HttpRequestError as exc:
                if exc.status_code is not None and exc.status_code < 500:
                    raise
                last_error = exc
            except (TimeoutError, URLError, OSError) as exc:
                last_error = exc

            if attempt < self.max_attempts and self.backoff_seconds > 0:
                time.sleep(self.backoff_seconds * attempt)

        if isinstance(last_error, HttpRequestError):
            raise last_error
        raise HttpRequestError(path=path, status_code=None, message=str(last_error))


class UrlopenTransport:
    def __init__(self, *, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def __call__(
        self,
        path: str,
        query: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        url = f"{self.base_url}{path}?{urlencode(query)}"
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                return HttpResponse(
                    status_code=response.status,
                    headers={key: value for key, value in response.headers.items()},
                    body=response.read(),
                )
        except HTTPError as exc:
            return HttpResponse(
                status_code=exc.code,
                headers={key: value for key, value in exc.headers.items()},
                body=exc.read(),
            )


def _json_or_error(path: str, response: HttpResponse) -> dict[str, Any]:
    body_text = response.body.decode("utf-8") if response.body else ""
    if response.status_code == 429:
        raise RateLimitExceeded(
            path=path,
            status_code=response.status_code,
            headers=response.headers,
            message=_error_message(body_text),
        )
    if response.status_code < 200 or response.status_code >= 300:
        raise HttpRequestError(path=path, status_code=response.status_code, message=_error_message(body_text))
    try:
        decoded = json.loads(body_text or "{}")
    except json.JSONDecodeError as exc:
        raise HttpRequestError(path=path, status_code=response.status_code, message=f"invalid json: {exc}") from exc
    if not isinstance(decoded, dict):
        raise HttpRequestError(path=path, status_code=response.status_code, message="expected json object")
    return decoded


def _error_message(body_text: str) -> str:
    try:
        decoded = json.loads(body_text or "{}")
    except json.JSONDecodeError:
        return body_text
    error = decoded.get("errors", {}).get("error") if isinstance(decoded, dict) else None
    if isinstance(error, list):
        return "; ".join(str(item) for item in error)
    if error:
        return str(error)
    return body_text


def _cache_key(path: str, query: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return path, tuple(sorted((key, str(value)) for key, value in query.items()))


def _header(headers: dict[str, str], name: str) -> str | None:
    expected = name.lower()
    for key, value in headers.items():
        if key.lower() == expected:
            return value
    return None
