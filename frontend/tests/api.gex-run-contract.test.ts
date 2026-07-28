import type { GexRun } from "@/lib/api";

const runContract: GexRun = {
  id: 42,
  chain_capture_id: 7,
  metric_name: "gex_proxy",
  ticker: "SPY",
  scope: "all",
  position_assumption: "call-positive/put-negative",
  pricing_model: "black_scholes",
  model_version: "gex_proxy_v1",
  calendar_version: "nyse_rules_v2",
  option_close_policy_version: "option_close_policy_v1",
  risk_free_rate: 0.05,
  risk_free_rate_source: "settings",
  dividend_yield: 0,
  dividend_yield_source: "underlyings",
  spot_price: 500,
  spot_observed_at: null,
  spot_provider_received_at: "2026-07-27T14:00:00+00:00",
  chain_status: "success",
  chain_min_dte: 0,
  chain_max_dte: 45,
  requested_expirations: ["2026-07-27"],
  included_expirations: ["2026-07-27"],
  chain_captured_at: "2026-07-27T14:00:00+00:00",
  analyzed_at: "2026-07-27T14:00:15+00:00",
  net_gex_proxy: 0,
  zero_gamma_proxy: null,
  call_wall_proxy: null,
  put_wall_proxy: null,
  excluded_contract_count: 0,
  persist_strike_rows: false,
  iv_time_summary: { min: null, max: null, known_count: 0, unknown_count: 0 },
  oi_time_summary: { min: null, max: null, known_count: 0, unknown_count: 0 },
  warnings: []
};

const chainCaptureId: number = runContract.chain_capture_id;

void chainCaptureId;
