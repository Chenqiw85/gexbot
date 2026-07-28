import type { GexRow, GexRun, ObservedSummary } from "./api";

export type ModelInputLabel =
  | "Pricing"
  | "Model"
  | "Calendar"
  | "Option Policy"
  | "Rate"
  | "Dividend"
  | "Assumption";

export type ModelInputItem = {
  label: ModelInputLabel;
  value: string;
};

export type StrikeTableColumn = {
  label: string;
  className?: "positive" | "negative";
  render: (row: GexRow) => string;
};

export type HistoryRunSummaryItem = {
  label: "Net" | "Zero" | "Call" | "Put";
  value: string;
};

export function modelInputItems(run: GexRun | null | undefined): ModelInputItem[] {
  return [
    { label: "Pricing", value: run?.pricing_model ?? "unknown" },
    { label: "Model", value: run?.model_version ?? "unknown" },
    { label: "Calendar", value: run?.calendar_version ?? "unknown" },
    { label: "Option Policy", value: run?.option_close_policy_version ?? "unknown" },
    {
      label: "Rate",
      value: run ? `${(run.risk_free_rate * 100).toFixed(2)}% (${run.risk_free_rate_source})` : "unknown"
    },
    {
      label: "Dividend",
      value: run ? `${(run.dividend_yield * 100).toFixed(2)}% (${run.dividend_yield_source})` : "unknown"
    },
    { label: "Assumption", value: run?.position_assumption ?? "unknown" }
  ];
}

export function historyRunSummaryItems(
  run: GexRun,
  formatGex: (value: number) => string = (value) => String(value),
): HistoryRunSummaryItem[] {
  return [
    { label: "Net", value: formatGex(run.net_gex_proxy) },
    { label: "Zero", value: formatNullableLevel(run.zero_gamma_proxy) },
    { label: "Call", value: formatNullableLevel(run.call_wall_proxy) },
    { label: "Put", value: formatNullableLevel(run.put_wall_proxy) },
  ];

  function formatNullableLevel(value: number | null): string {
    return value == null ? "n/a" : value.toFixed(2);
  }
}

export function strikeTableColumns(formatGex: (value: number) => string = (value) => String(value)): StrikeTableColumn[] {
  return [
    { label: "Strike", render: (row) => row.strike.toFixed(0) },
    { label: "Call", className: "positive", render: (row) => formatGex(row.call_gex_proxy) },
    { label: "Put", className: "negative", render: (row) => formatGex(row.put_gex_proxy) },
    { label: "Net", render: (row) => formatGex(row.net_gex_proxy) },
    { label: "Call OI", render: (row) => String(row.call_oi) },
    { label: "Put OI", render: (row) => String(row.put_oi) },
    { label: "Call Vol", render: (row) => String(row.call_volume) },
    { label: "Put Vol", render: (row) => String(row.put_volume) }
  ];
}

export function observedSummaryLabel(summary: ObservedSummary | null | undefined): string {
  if (!summary) {
    return "unknown";
  }

  const total = summary.known_count + summary.unknown_count;
  const coverage = total > 0 ? ` (${summary.known_count}/${total} known)` : "";
  if (summary.known_count === 0) {
    return `unknown${coverage}`;
  }
  if (summary.min === summary.max) {
    return `${summary.min ?? "unknown"}${coverage}`;
  }
  return `${summary.min ?? "unknown"} - ${summary.max ?? "unknown"}${coverage}`;
}
