import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.domain.calendar import (
    DEFAULT_CALENDAR_VERSION,
    CalendarVersion,
    MarketCalendar,
    OptionClosePolicy,
    build_expiration_times,
)


NY = ZoneInfo("America/New_York")


class CalendarPolicyTest(unittest.TestCase):
    def test_default_calendar_version_tracks_special_closure_table(self):
        self.assertEqual(str(DEFAULT_CALENDAR_VERSION), "nyse_rules_v3")

    def test_spy_has_underlying_close_at_1600_and_option_last_trade_at_1615(self):
        expiration = date(2026, 7, 17)

        times = build_expiration_times("SPY", expiration, OptionClosePolicy.late_option_1615())

        self.assertEqual(times.underlying_close_at, datetime(2026, 7, 17, 16, 0, tzinfo=NY))
        self.assertEqual(times.option_last_trade_at, datetime(2026, 7, 17, 16, 15, tzinfo=NY))

    def test_standard_equity_has_both_underlying_and_option_close_at_1600(self):
        expiration = date(2026, 7, 17)

        times = build_expiration_times("AAPL", expiration, OptionClosePolicy.standard_1600())

        self.assertEqual(times.underlying_close_at, datetime(2026, 7, 17, 16, 0, tzinfo=NY))
        self.assertEqual(times.option_last_trade_at, datetime(2026, 7, 17, 16, 0, tzinfo=NY))

    def test_market_calendar_rejects_weekends_and_known_holidays(self):
        calendar = MarketCalendar(version=CalendarVersion("test_calendar_v1"))

        self.assertFalse(calendar.is_trading_day(date(2026, 7, 4)))
        self.assertFalse(calendar.is_trading_day(date(2026, 7, 3)))
        self.assertTrue(calendar.is_trading_day(date(2026, 7, 6)))

    def test_market_calendar_includes_2025_official_special_closure_and_half_days(self):
        calendar = MarketCalendar()

        self.assertFalse(calendar.is_trading_day(date(2025, 1, 9)))

        july_session = calendar.session_for(date(2025, 7, 3))
        thanksgiving_session = calendar.session_for(date(2025, 11, 28))
        christmas_session = calendar.session_for(date(2025, 12, 24))

        self.assertTrue(july_session.is_half_day)
        self.assertEqual(july_session.underlying_close_at, datetime(2025, 7, 3, 13, 0, tzinfo=NY))
        self.assertTrue(thanksgiving_session.is_half_day)
        self.assertEqual(thanksgiving_session.underlying_close_at, datetime(2025, 11, 28, 13, 0, tzinfo=NY))
        self.assertTrue(christmas_session.is_half_day)
        self.assertEqual(christmas_session.underlying_close_at, datetime(2025, 12, 24, 13, 0, tzinfo=NY))

    def test_half_day_closes_early_for_both_underlying_and_late_option_policy(self):
        calendar = MarketCalendar(version=CalendarVersion("test_calendar_v1"))
        expiration = date(2026, 11, 27)

        times = build_expiration_times(
            "SPY",
            expiration,
            OptionClosePolicy.late_option_1615(),
            market_calendar=calendar,
        )

        self.assertEqual(times.underlying_close_at, datetime(2026, 11, 27, 13, 0, tzinfo=NY))
        self.assertEqual(times.option_last_trade_at, datetime(2026, 11, 27, 13, 15, tzinfo=NY))

    def test_market_calendar_includes_official_2027_holidays_and_half_days(self):
        calendar = MarketCalendar()

        self.assertFalse(calendar.is_trading_day(date(2027, 1, 1)))
        self.assertFalse(calendar.is_trading_day(date(2027, 3, 26)))
        self.assertFalse(calendar.is_trading_day(date(2027, 6, 18)))
        self.assertFalse(calendar.is_trading_day(date(2027, 7, 5)))
        self.assertFalse(calendar.is_trading_day(date(2027, 12, 24)))

        session = calendar.session_for(date(2027, 11, 26))

        self.assertTrue(session.is_half_day)
        self.assertEqual(session.underlying_close_at, datetime(2027, 11, 26, 13, 0, tzinfo=NY))

    def test_market_calendar_includes_official_2028_holidays_and_half_days(self):
        calendar = MarketCalendar()

        self.assertTrue(calendar.is_trading_day(date(2028, 1, 3)))
        self.assertFalse(calendar.is_trading_day(date(2028, 4, 14)))
        self.assertFalse(calendar.is_trading_day(date(2028, 6, 19)))

        july_session = calendar.session_for(date(2028, 7, 3))
        thanksgiving_session = calendar.session_for(date(2028, 11, 24))

        self.assertTrue(july_session.is_half_day)
        self.assertEqual(july_session.underlying_close_at, datetime(2028, 7, 3, 13, 0, tzinfo=NY))
        self.assertTrue(thanksgiving_session.is_half_day)
        self.assertEqual(thanksgiving_session.underlying_close_at, datetime(2028, 11, 24, 13, 0, tzinfo=NY))

    def test_market_calendar_generates_future_holidays_and_half_days(self):
        calendar = MarketCalendar()

        self.assertFalse(calendar.is_trading_day(date(2029, 1, 1)))
        self.assertFalse(calendar.is_trading_day(date(2029, 3, 30)))
        self.assertFalse(calendar.is_trading_day(date(2029, 7, 4)))
        self.assertFalse(calendar.is_trading_day(date(2029, 11, 22)))
        self.assertFalse(calendar.is_trading_day(date(2029, 12, 25)))

        july_session = calendar.session_for(date(2029, 7, 3))
        thanksgiving_session = calendar.session_for(date(2029, 11, 23))
        christmas_session = calendar.session_for(date(2029, 12, 24))

        self.assertTrue(july_session.is_half_day)
        self.assertEqual(july_session.underlying_close_at, datetime(2029, 7, 3, 13, 0, tzinfo=NY))
        self.assertTrue(thanksgiving_session.is_half_day)
        self.assertEqual(thanksgiving_session.underlying_close_at, datetime(2029, 11, 23, 13, 0, tzinfo=NY))
        self.assertTrue(christmas_session.is_half_day)
        self.assertEqual(christmas_session.underlying_close_at, datetime(2029, 12, 24, 13, 0, tzinfo=NY))


if __name__ == "__main__":
    unittest.main()
