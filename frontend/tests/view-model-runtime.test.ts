import assert from "node:assert/strict";

import type { GexRun } from "../lib/api";
import { historyRunSummaryItems, observedSummaryLabel } from "../lib/view-model";

const run = {
  id: 1,
  chain_capture_id: 7,
  metric_name: "gex_proxy",
  ticker: "SPY",
  scope: "all",
  position_assumption: "call_positive_put_negative",
  pricing_model: "black_scholes_v1",
  model_version: "black_scholes_v1.0.0",
  calendar_version: "nyse_rules_v3",
  option_close_policy_version: "option_close_policy_v1",
  risk_free_rate: 0.05,
  risk_free_rate_source: "configured",
  dividend_yield: 0.01,
  dividend_yield_source: "configured",
  spot_price: 500,
  spot_observed_at: null,
  spot_provider_received_at: "2026-07-27T10:00:02-04:00",
  chain_status: "success",
  chain_min_dte: 0,
  chain_max_dte: 45,
  requested_expirations: ["2026-07-27"],
  included_expirations: ["2026-07-27"],
  chain_captured_at: "2026-07-27T10:00:00-04:00",
  analyzed_at: "2026-07-27T10:00:03-04:00",
  net_gex_proxy: 1234567,
  zero_gamma_proxy: 501.25,
  call_wall_proxy: 510,
  put_wall_proxy: null,
  excluded_contract_count: 0,
  persist_strike_rows: false,
  iv_time_summary: { min: null, max: null, known_count: 0, unknown_count: 2 },
  oi_time_summary: { min: "2026-07-26", max: "2026-07-26", known_count: 2, unknown_count: 0 },
  warnings: []
} satisfies GexRun;

assert.deepEqual(historyRunSummaryItems(run, (value) => `${Math.round(value / 1000)}k`), [
  { label: "Net", value: "1235k" },
  { label: "Zero", value: "501.25" },
  { label: "Call", value: "510.00" },
  { label: "Put", value: "n/a" }
]);

assert.equal(observedSummaryLabel(run.iv_time_summary), "unknown (0/2 known)");
assert.equal(observedSummaryLabel(run.oi_time_summary), "2026-07-26 (2/2 known)");
assert.equal(observedSummaryLabel(undefined), "unknown");
