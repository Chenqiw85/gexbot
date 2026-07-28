from __future__ import annotations

from dataclasses import dataclass
import re


_TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*$")


@dataclass(frozen=True)
class AppTicker:
    ticker: str
    dividend_yield: float
    enabled: bool = True


def normalize_ticker(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError("ticker is required")
    if len(normalized) > 12 or not _TICKER_PATTERN.fullmatch(normalized):
        raise ValueError("ticker must be 1-12 chars using letters, digits, dot, or hyphen")
    return normalized
