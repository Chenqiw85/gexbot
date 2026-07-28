from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import cache
from typing import NewType
from zoneinfo import ZoneInfo


NY = ZoneInfo("America/New_York")
CalendarVersion = NewType("CalendarVersion", str)
DEFAULT_CALENDAR_VERSION = CalendarVersion("nyse_rules_v3")
_SPECIAL_FULL_CLOSURES = frozenset(
    {
        date(2025, 1, 9),  # National Day of Mourning for President Jimmy Carter.
    }
)


@dataclass(frozen=True)
class ExpirationTimes:
    underlying_close_at: datetime
    option_last_trade_at: datetime


@dataclass(frozen=True)
class OptionClosePolicy:
    name: str
    option_extra_minutes: int
    version: str

    @classmethod
    def standard_1600(cls) -> "OptionClosePolicy":
        return cls(name="standard_1600", option_extra_minutes=0, version="option_close_policy_v1")

    @classmethod
    def late_option_1615(cls) -> "OptionClosePolicy":
        return cls(name="late_option_1615", option_extra_minutes=15, version="option_close_policy_v1")


@dataclass(frozen=True)
class MarketSession:
    session_date: date
    open_at: datetime
    underlying_close_at: datetime
    is_half_day: bool


class MarketCalendar:
    """Deterministic NYSE regular-session calendar for scheduler gating.

    The service boundary carries a calendar version so historical analysis runs
    can be recomputed against the same calendar policy that produced them.
    """

    def __init__(self, version: CalendarVersion = DEFAULT_CALENDAR_VERSION) -> None:
        self.version = version

    def is_trading_day(self, day: date) -> bool:
        if day.weekday() >= 5:
            return False
        if day in _SPECIAL_FULL_CLOSURES:
            return False
        if day in _full_holidays_for_year(day.year):
            return False
        return True

    def session_for(self, day: date) -> MarketSession:
        if not self.is_trading_day(day):
            raise ValueError(f"{day.isoformat()} is not a trading day")

        is_half_day = day in _half_days_for_year(day.year)
        close_clock = time(13, 0) if is_half_day else time(16, 0)
        return MarketSession(
            session_date=day,
            open_at=datetime.combine(day, time(9, 30), NY),
            underlying_close_at=datetime.combine(day, close_clock, NY),
            is_half_day=is_half_day,
        )

    def current_session(self, now: datetime) -> MarketSession | None:
        local_now = now.astimezone(NY)
        if not self.is_trading_day(local_now.date()):
            return None
        session = self.session_for(local_now.date())
        if session.open_at <= local_now <= session.underlying_close_at:
            return session
        return None


def policy_for_ticker(ticker: str, late_close_roots: set[str] | None = None) -> OptionClosePolicy:
    roots = late_close_roots or {"SPY", "QQQ"}
    if ticker.upper() in roots:
        return OptionClosePolicy.late_option_1615()
    return OptionClosePolicy.standard_1600()


def build_expiration_times(
    ticker: str,
    expiration: date,
    policy: OptionClosePolicy,
    *,
    market_calendar: MarketCalendar | None = None,
) -> ExpirationTimes:
    calendar = market_calendar or MarketCalendar()
    underlying_close = _underlying_close_for_expiration(expiration, calendar)
    option_last_trade = underlying_close + timedelta(minutes=policy.option_extra_minutes)
    return ExpirationTimes(
        underlying_close_at=underlying_close,
        option_last_trade_at=option_last_trade,
    )


def _underlying_close_for_expiration(expiration: date, calendar: MarketCalendar) -> datetime:
    if calendar.is_trading_day(expiration):
        return calendar.session_for(expiration).underlying_close_at
    return datetime.combine(expiration, time(16, 0), NY)


@cache
def _full_holidays_for_year(year: int) -> frozenset[date]:
    holidays = {
        _new_years_day_observed(year),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed_weekday_holiday(date(year, 6, 19)),
        _observed_weekday_holiday(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_weekday_holiday(date(year, 12, 25)),
    }
    return frozenset(day for day in holidays if day is not None and day.year == year)


@cache
def _half_days_for_year(year: int) -> frozenset[date]:
    candidates = {
        _independence_day_early_close(year),
        _nth_weekday(year, 11, 3, 4) + timedelta(days=1),
        date(year, 12, 24),
    }
    return frozenset(
        day
        for day in candidates
        if day is not None
        and day.year == year
        and day.weekday() < 5
        and day not in _full_holidays_for_year(year)
    )


def _new_years_day_observed(year: int) -> date | None:
    new_year = date(year, 1, 1)
    if new_year.weekday() == 5:
        return None
    if new_year.weekday() == 6:
        return new_year + timedelta(days=1)
    return new_year


def _observed_weekday_holiday(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _independence_day_early_close(year: int) -> date | None:
    independence_day = date(year, 7, 4)
    if independence_day.weekday() in {1, 2, 3, 4}:
        return date(year, 7, 3)
    return None


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    day = date(year, month, 1)
    offset = (weekday - day.weekday()) % 7
    return day + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        day = date(year, month + 1, 1) - timedelta(days=1)
    return day - timedelta(days=(day.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)
