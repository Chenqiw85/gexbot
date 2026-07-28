from __future__ import annotations

from datetime import date, datetime, timezone
import math
from typing import Any

from app.domain.gex import OptionType
from app.providers.base import MarketDataProvider, ProviderDataError, ProviderOptionContract, SpotQuote
from app.providers.http import MemoryResponseCache, ReliableHttpClient, UrlopenTransport


class TradierProvider(MarketDataProvider):
    source = "tradier"

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        http_client: ReliableHttpClient | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http_client = http_client or ReliableHttpClient(
            transport=UrlopenTransport(base_url=self.base_url),
            cache=MemoryResponseCache(ttl_seconds=cache_ttl_seconds),
        )

    def available_expirations(self, ticker: str) -> list[date]:
        tradier_symbol = _tradier_market_symbol(ticker)
        payload = self._get(
            "/markets/options/expirations",
            {"symbol": tradier_symbol, "includeAllRoots": "true", "strikes": "false"},
        )
        expirations = payload.get("expirations", {})
        if not isinstance(expirations, dict):
            raise ProviderDataError(f"Tradier expirations payload for {ticker.upper()} is malformed")
        dates = expirations.get("date", [])
        if isinstance(dates, str):
            dates = [dates]
        if not isinstance(dates, list):
            raise ProviderDataError(f"Tradier expirations date payload for {ticker.upper()} is malformed")
        parsed_dates = []
        for item in dates:
            try:
                parsed_dates.append(date.fromisoformat(str(item)))
            except (TypeError, ValueError):
                raise ProviderDataError(
                    f"Tradier expiration date {item!r} for {ticker.upper()} is not ISO formatted"
                ) from None
        return sorted(parsed_dates)

    def option_chain_for_expiration(self, ticker: str, expiration: date) -> list[ProviderOptionContract]:
        tradier_symbol = _tradier_market_symbol(ticker)
        payload = self._get(
            "/markets/options/chains",
            {"symbol": tradier_symbol, "expiration": expiration.isoformat(), "greeks": "true"},
        )
        options_payload = payload.get("options", {})
        if not isinstance(options_payload, dict):
            raise ProviderDataError(
                f"Tradier options payload for {ticker.upper()} {expiration.isoformat()} is malformed"
            )
        options = options_payload.get("option", [])
        if isinstance(options, dict):
            options = [options]
        if not isinstance(options, list):
            raise ProviderDataError(
                f"Tradier option list for {ticker.upper()} {expiration.isoformat()} is malformed"
            )

        contracts: list[ProviderOptionContract] = []
        for option in options:
            if not isinstance(option, dict):
                raise ProviderDataError(
                    f"Tradier option item for {ticker.upper()} {expiration.isoformat()} is malformed"
                )
            greeks = option.get("greeks") or {}
            if not isinstance(greeks, dict):
                option_label = option.get("symbol") or ticker.upper()
                raise ProviderDataError(
                    f"Tradier greeks payload for {option_label} expiring {expiration.isoformat()} is malformed"
                )
            option_symbol = _required_string(option.get("symbol"), field_name="symbol", expiration=expiration)
            option_type = _parse_option_type(option.get("option_type"), option_symbol=option_symbol, expiration=expiration)
            contracts.append(
                ProviderOptionContract(
                    option_symbol=option_symbol,
                    expiration_date=expiration,
                    strike=_required_float(
                        option.get("strike"),
                        field_name="strike",
                        option_symbol=option_symbol,
                        expiration=expiration,
                    ),
                    option_type=option_type,
                    open_interest=_maybe_int(option.get("open_interest"), field_name="open_interest"),
                    volume=_maybe_int(option.get("volume"), field_name="volume"),
                    implied_volatility=_maybe_float(
                        _first_present(greeks, "mid_iv", "smv_vol"),
                        field_name="iv",
                    ),
                    iv_observed_at=None,
                    oi_observed_date=None,
                    contract_multiplier=_maybe_int(option.get("contract_size"), field_name="contract_size"),
                    bid=_maybe_float(option.get("bid"), field_name="bid"),
                    ask=_maybe_float(option.get("ask"), field_name="ask"),
                    last=_maybe_float(option.get("last"), field_name="last"),
                    mark=_maybe_float(option.get("mark"), field_name="mark"),
                    vendor_delta=_maybe_float(greeks.get("delta"), field_name="delta"),
                    vendor_gamma=_maybe_float(greeks.get("gamma"), field_name="gamma"),
                    vendor_theta=_maybe_float(greeks.get("theta"), field_name="theta"),
                    vendor_vega=_maybe_float(greeks.get("vega"), field_name="vega"),
                )
            )
        return contracts

    def spot_quote(self, ticker: str) -> SpotQuote:
        normalized_ticker = ticker.upper()
        tradier_symbol = _tradier_market_symbol(ticker)
        payload = self._get("/markets/quotes", {"symbols": tradier_symbol}, use_cache=False)
        quotes = payload.get("quotes", {})
        if not isinstance(quotes, dict):
            raise ProviderDataError(f"Tradier quotes payload for {ticker.upper()} is malformed")
        quote = quotes.get("quote", {})
        if isinstance(quote, list):
            quote = quote[0] if quote else {}
        if not isinstance(quote, dict):
            raise ProviderDataError(f"Tradier quote payload for {ticker.upper()} is malformed")
        price = quote.get("last")
        uses_last_price = price not in (None, "")
        if uses_last_price:
            observed_at = _maybe_epoch_datetime(quote.get("trade_date"), field_name="trade_date")
        else:
            price = quote.get("close")
            observed_at = None
        if price in (None, ""):
            raise ProviderDataError(f"Tradier quote for {ticker.upper()} does not include last or close")
        try:
            spot_price = float(price)
        except (TypeError, ValueError):
            raise ProviderDataError(f"Tradier quote for {ticker.upper()} price {price!r} is not numeric") from None
        if spot_price <= 0:
            raise ProviderDataError(f"Tradier quote for {ticker.upper()} price {price!r} is not positive")
        received_at = datetime.now(timezone.utc)
        return SpotQuote(
            ticker=normalized_ticker,
            spot_price=spot_price,
            spot_observed_at=observed_at,
            provider_received_at=received_at,
        )

    def _get(self, path: str, query: dict[str, str], *, use_cache: bool = True) -> dict[str, Any]:
        return self.http_client.get_json(
            path,
            query,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            },
            use_cache=use_cache,
        )


def _maybe_float(value: Any, *, field_name: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ProviderDataError(f"Tradier field {field_name} value {value!r} is not numeric") from None


def _tradier_market_symbol(ticker: str) -> str:
    return ticker.upper().replace(".", "/")


def _first_present(payload: dict[str, Any], *field_names: str) -> Any:
    for field_name in field_names:
        value = payload.get(field_name)
        if value not in (None, ""):
            return value
    return None


def _maybe_int(value: Any, *, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ProviderDataError(f"Tradier field {field_name} value {value!r} is not numeric") from None


def _maybe_epoch_datetime(value: Any, *, field_name: str) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        raise ProviderDataError(f"Tradier field {field_name} value {value!r} is not numeric") from None
    if not math.isfinite(timestamp) or timestamp <= 0:
        raise ProviderDataError(f"Tradier field {field_name} value {value!r} must be a positive timestamp")
    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        raise ProviderDataError(f"Tradier field {field_name} value {value!r} is outside supported timestamp range") from None


def _parse_option_type(value: Any, *, option_symbol: str, expiration: date) -> OptionType:
    if value == "call":
        return OptionType.CALL
    if value == "put":
        return OptionType.PUT
    raise ProviderDataError(
        f"Tradier option {option_symbol} expiring {expiration.isoformat()} has unsupported option_type {value!r}"
    )


def _required_string(value: Any, *, field_name: str, expiration: date) -> str:
    if value in (None, ""):
        raise ProviderDataError(f"Tradier option expiring {expiration.isoformat()} is missing {field_name}")
    return str(value)


def _required_float(value: Any, *, field_name: str, option_symbol: str, expiration: date) -> float:
    if value in (None, ""):
        raise ProviderDataError(
            f"Tradier option {option_symbol} expiring {expiration.isoformat()} is missing {field_name}"
        )
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ProviderDataError(
            f"Tradier option {option_symbol} expiring {expiration.isoformat()} {field_name} {value!r} is not numeric"
        ) from None
