import assert from "node:assert/strict";

import {
  CLOSED_MARKET_STATUS_REFRESH_SECONDS,
  dashboardRefreshIntervalMs,
  type MarketStatus
} from "../lib/api";

const etfLateOptionWindow = {
  ticker: "SPY",
  calendar_version: "nyse_rules_v3",
  market_date: "2026-07-27",
  is_trading_day: true,
  is_half_day: false,
  is_underlying_market_open: false,
  is_option_market_open: true,
  underlying_open_at: "2026-07-27T09:30:00-04:00",
  underlying_close_at: "2026-07-27T16:00:00-04:00",
  option_last_trade_at: "2026-07-27T16:15:00-04:00",
  option_last_trade_policy: "late_option_1615",
  chain_refresh_seconds: 900,
  spot_refresh_seconds: 15
} satisfies MarketStatus;

assert.equal(dashboardRefreshIntervalMs(etfLateOptionWindow), 15_000);
assert.equal(
  dashboardRefreshIntervalMs({ ...etfLateOptionWindow, is_option_market_open: false }),
  CLOSED_MARKET_STATUS_REFRESH_SECONDS * 1000
);
assert.equal(dashboardRefreshIntervalMs(null), CLOSED_MARKET_STATUS_REFRESH_SECONDS * 1000);
