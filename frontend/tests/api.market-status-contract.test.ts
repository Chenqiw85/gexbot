import { dashboardRefreshIntervalMs, fetchMarketStatus, nyMarketDateInputValue, type MarketStatus } from "@/lib/api";

async function marketStatusContract() {
  const status: MarketStatus = await fetchMarketStatus("spy");
  const underlyingOpen: boolean = status.is_underlying_market_open;
  const optionOpen: boolean = status.is_option_market_open;
  const marketDate: string = status.market_date;
  const spotRefreshSeconds: number = status.spot_refresh_seconds;
  const optionLastTradeAt: string | null = status.option_last_trade_at;
  const openIntervalMs: number = dashboardRefreshIntervalMs(status);
  const closedIntervalMs: number = dashboardRefreshIntervalMs({ ...status, is_underlying_market_open: false });
  const unknownIntervalMs: number = dashboardRefreshIntervalMs(null);
  const defaultHistoryDate: string = nyMarketDateInputValue(new Date("2026-07-28T01:00:00Z"));

  return {
    underlyingOpen,
    optionOpen,
    marketDate,
    spotRefreshSeconds,
    optionLastTradeAt,
    openIntervalMs,
    closedIntervalMs,
    unknownIntervalMs,
    defaultHistoryDate
  };
}

void marketStatusContract;
