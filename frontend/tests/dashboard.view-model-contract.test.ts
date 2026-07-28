import type { GexRun } from "@/lib/api";
import {
  historyRunSummaryItems,
  modelInputItems,
  observedSummaryLabel,
  strikeTableColumns,
  type HistoryRunSummaryItem,
  type ModelInputLabel,
  type StrikeTableColumn
} from "@/lib/view-model";

const run = {
  id: 1,
  chain_capture_id: 7,
  metric_name: "gex_proxy",
  ticker: "SPY",
  scope: "all",
  position_assumption: "call_positive_put_negative",
  pricing_model: "black_scholes_v1",
  model_version: "black_scholes_v1.0.0",
  calendar_version: "nyse_rules_v2",
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
  net_gex_proxy: 1000,
  zero_gamma_proxy: 501,
  call_wall_proxy: 510,
  put_wall_proxy: 490,
  excluded_contract_count: 0,
  persist_strike_rows: false,
  iv_time_summary: { min: null, max: null, known_count: 0, unknown_count: 2 },
  oi_time_summary: { min: "2026-07-26", max: "2026-07-26", known_count: 2, unknown_count: 0 },
  warnings: [],
  rows: [
    {
      strike: 500,
      call_gex_proxy: 1200,
      put_gex_proxy: -200,
      net_gex_proxy: 1000,
      call_oi: 100,
      put_oi: 90,
      call_volume: 11,
      put_volume: 13
    }
  ]
} satisfies GexRun;

const labels: ModelInputLabel[] = modelInputItems(run).map((item) => item.label);
const columns: StrikeTableColumn[] = strikeTableColumns();
const historyItems: HistoryRunSummaryItem[] = historyRunSummaryItems(run, (value) => value.toFixed(0));
const ivObservedLabel: string = observedSummaryLabel(run.iv_time_summary);
const oiObservedLabel: string = observedSummaryLabel(run.oi_time_summary);
const missingObservedLabel: string = observedSummaryLabel(undefined);

void labels;
void columns;
void historyItems;
void ivObservedLabel;
void oiObservedLabel;
void missingObservedLabel;
