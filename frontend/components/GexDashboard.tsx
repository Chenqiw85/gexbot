"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import ReactECharts from "echarts-for-react";
import { Activity, Clock3, Database, History, Plus, RefreshCw, Save, Search, Trash2 } from "lucide-react";

import {
  addTicker,
  analyzeNow,
  captureChain,
  dashboardRefreshIntervalMs,
  fetchHistory,
  fetchLatest,
  fetchMarketStatus,
  fetchRun,
  fetchTickers,
  isNoLatestRunError,
  nyMarketDateInputValue,
  persistRunRows,
  updateTicker,
  type AppTicker,
  type GexRun,
  type MarketStatus,
  type Scope
} from "@/lib/api";
import { dividendYieldFromPercentInput } from "@/lib/form-utils";
import { historyRunSummaryItems, modelInputItems, observedSummaryLabel, strikeTableColumns } from "@/lib/view-model";

const TICKERS = ["SPY", "QQQ"];
const DEFAULT_TICKER_SET = new Set(TICKERS);

export function GexDashboard() {
  const [ticker, setTicker] = useState("SPY");
  const [tickers, setTickers] = useState<AppTicker[]>(() =>
    TICKERS.map((item) => ({ ticker: item, dividend_yield: 0, enabled: true }))
  );
  const [customTicker, setCustomTicker] = useState("");
  const [customDividendYieldPercent, setCustomDividendYieldPercent] = useState("");
  const [scope, setScope] = useState<Scope>("all");
  const [minDte, setMinDte] = useState(0);
  const [maxDte, setMaxDte] = useState(45);
  const [run, setRun] = useState<GexRun | null>(null);
  const [historyDate, setHistoryDate] = useState(() => nyMarketDateInputValue());
  const [historyRuns, setHistoryRuns] = useState<GexRun[]>([]);
  const [marketStatus, setMarketStatus] = useState<MarketStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const tickerSymbols = useMemo(
    () => [...TICKERS, ...tickers.map((item) => item.ticker), ticker].filter(unique),
    [tickers, ticker]
  );
  const strikeColumns = useMemo(() => strikeTableColumns(compact), []);

  const loadLatest = useCallback(async () => {
    try {
      setError(null);
      const next = await fetchLatest(ticker, scope, true);
      setRun(next);
    } catch (err) {
      if (isNoLatestRunError(err)) {
        setRun(null);
        return;
      }
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  }, [ticker, scope]);

  const loadMarketStatus = useCallback(async (): Promise<MarketStatus | null> => {
    try {
      const status = await fetchMarketStatus(ticker);
      setMarketStatus(status);
      return status;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
      return null;
    }
  }, [ticker]);

  useEffect(() => {
    let cancelled = false;
    async function loadTickers() {
      try {
        const result = await fetchTickers();
        if (!cancelled) {
          setTickers(result.tickers);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Unknown error");
        }
      }
    }

    loadTickers();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    loadLatest();
    loadMarketStatus();
  }, [loadLatest, loadMarketStatus]);

  useEffect(() => {
    const intervalMs = dashboardRefreshIntervalMs(marketStatus);
    const interval = window.setInterval(() => {
      loadMarketStatus().then((status) => {
        if (status?.is_option_market_open) {
          loadLatest();
        }
      });
    }, intervalMs);
    return () => window.clearInterval(interval);
  }, [loadLatest, loadMarketStatus, marketStatus?.is_option_market_open, marketStatus?.spot_refresh_seconds]);

  const chartOptions = useMemo(() => {
    const rows = run?.rows ?? [];
    return {
      grid: { top: 20, right: 30, bottom: 36, left: 74 },
      tooltip: { trigger: "axis" },
      legend: { top: 0, textStyle: { color: "#334155" } },
      xAxis: {
        type: "category",
        data: rows.map((row) => row.strike.toFixed(0)),
        axisLabel: { color: "#475569" }
      },
      yAxis: {
        type: "value",
        axisLabel: {
          color: "#475569",
          formatter: (value: number) => compact(value)
        },
        splitLine: { lineStyle: { color: "#e2e8f0" } }
      },
      series: [
        {
          name: "Call GEX proxy",
          type: "bar",
          stack: "gex",
          data: rows.map((row) => row.call_gex_proxy),
          itemStyle: { color: "#15803d" }
        },
        {
          name: "Put GEX proxy",
          type: "bar",
          stack: "gex",
          data: rows.map((row) => row.put_gex_proxy),
          itemStyle: { color: "#b91c1c" }
        },
        {
          name: "Net GEX proxy",
          type: "line",
          data: rows.map((row) => row.net_gex_proxy),
          symbolSize: 4,
          lineStyle: { color: "#2563eb", width: 2 }
        }
      ]
    };
  }, [run]);

  async function handleSpotRefresh() {
    setBusy(true);
    try {
      setRun(await analyzeNow(ticker, scope));
      await loadLatest();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setBusy(false);
    }
  }

  async function handleChainCapture() {
    setBusy(true);
    try {
      await captureChain(ticker, minDte, maxDte);
      setRun(await analyzeNow(ticker, scope));
      await loadLatest();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setBusy(false);
    }
  }

  async function handleLoadHistory() {
    setBusy(true);
    try {
      setError(null);
      const result = await fetchHistory(ticker, scope, historyDate, 2000);
      setHistoryRuns(result.runs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setBusy(false);
    }
  }

  async function handleLoadRun(runId: number) {
    setBusy(true);
    try {
      setError(null);
      setRun(await fetchRun(runId, true));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setBusy(false);
    }
  }

  async function handleCheckpoint() {
    if (!run) {
      return;
    }
    setBusy(true);
    try {
      setError(null);
      setRun(await persistRunRows(run.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setBusy(false);
    }
  }

  async function applyCustomTicker() {
    const normalized = customTicker.trim().toUpperCase();
    if (!normalized) {
      return;
    }

    setBusy(true);
    try {
      setError(null);
      const dividendYield = dividendYieldFromPercentInput(customDividendYieldPercent);
      const created = await addTicker(normalized, dividendYield);
      setTickers((current) => upsertTicker(current, created));
      setTicker(created.ticker);
      setCustomTicker("");
      setCustomDividendYieldPercent("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setBusy(false);
    }
  }

  async function handleDisableTicker(symbol: string) {
    if (DEFAULT_TICKER_SET.has(symbol)) {
      return;
    }

    setBusy(true);
    try {
      setError(null);
      const updated = await updateTicker(symbol, { enabled: false });
      setTickers((current) => current.filter((item) => item.ticker !== updated.ticker));
      if (ticker === updated.ticker) {
        setTicker("SPY");
        setRun(null);
        setHistoryRuns([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell">
      <section className="toolbar" aria-label="Dashboard controls">
        <div className="brand">
          <Activity size={22} aria-hidden="true" />
          <div>
            <h1>GEX Proxy Bot</h1>
            <p>call-positive / put-negative assumption</p>
          </div>
        </div>

        <div className="controls">
          <div className="ticker-strip" aria-label="Ticker selector">
            {tickerSymbols.map((item) => {
              const removable = !DEFAULT_TICKER_SET.has(item);
              return (
                <span key={item} className="ticker-pill">
                  <button className={item === ticker ? "active" : ""} onClick={() => setTicker(item)} type="button">
                    {item}
                  </button>
                  {removable ? (
                    <button
                      className="ticker-remove"
                      onClick={() => handleDisableTicker(item)}
                      disabled={busy}
                      type="button"
                      title={`Disable ${item}`}
                      aria-label={`Disable ${item}`}
                    >
                      <Trash2 size={14} aria-hidden="true" />
                    </button>
                  ) : null}
                </span>
              );
            })}
          </div>

          <div className="search">
            <Search size={16} aria-hidden="true" />
            <input
              value={customTicker}
              onChange={(event) => setCustomTicker(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") applyCustomTicker();
              }}
              placeholder="Ticker"
            />
            <input
              aria-label="Dividend yield percent"
              className="dividend-input"
              value={customDividendYieldPercent}
              onChange={(event) => setCustomDividendYieldPercent(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") applyCustomTicker();
              }}
              placeholder="Div %"
              type="number"
              min={0}
              max={100}
              step={0.01}
            />
            <button onClick={applyCustomTicker} disabled={busy || !customTicker.trim()} type="button" title="Register ticker">
              <Plus size={15} aria-hidden="true" />
            </button>
          </div>

          <div className="segmented">
            <button className={scope === "all" ? "active" : ""} onClick={() => setScope("all")} type="button">
              All
            </button>
            <button className={scope === "0dte" ? "active" : ""} onClick={() => setScope("0dte")} type="button">
              0DTE
            </button>
          </div>
        </div>
      </section>

      <section className="actions" aria-label="Refresh controls">
        <label>
          Min DTE
          <input value={minDte} min={0} max={365} type="number" onChange={(event) => setMinDte(Number(event.target.value))} />
        </label>
        <label>
          Max DTE
          <input value={maxDte} min={0} max={365} type="number" onChange={(event) => setMaxDte(Number(event.target.value))} />
        </label>
        <button onClick={handleSpotRefresh} disabled={busy} type="button" title="Refresh spot and recalculate">
          <RefreshCw size={16} aria-hidden="true" />
          Spot / Recalc
        </button>
        <button onClick={handleChainCapture} disabled={busy} type="button" title="Capture a fresh option chain">
          <Database size={16} aria-hidden="true" />
          Full / Partial Chain
        </button>
        <button
          onClick={handleCheckpoint}
          disabled={busy || !run || run.persist_strike_rows}
          type="button"
          title="Persist rows for a replay checkpoint"
        >
          <Save size={16} aria-hidden="true" />
          Checkpoint
        </button>
      </section>

      {error ? <div className="error">{error}</div> : null}

      <section className="status-grid" aria-label="Data timestamps">
        <StatusItem icon={<Clock3 size={16} />} label="Market" value={formatMarketStatus(marketStatus)} />
        <StatusItem icon={<Clock3 size={16} />} label="Spot Observed" value={formatTime(run?.spot_observed_at)} />
        <StatusItem icon={<Clock3 size={16} />} label="Spot Received" value={formatTime(run?.spot_provider_received_at)} />
        <StatusItem icon={<Clock3 size={16} />} label="IV" value={observedSummaryLabel(run?.iv_time_summary)} />
        <StatusItem icon={<Clock3 size={16} />} label="OI" value={observedSummaryLabel(run?.oi_time_summary)} />
        <StatusItem icon={<Clock3 size={16} />} label="Chain" value={formatTime(run?.chain_captured_at)} />
        <StatusItem icon={<Clock3 size={16} />} label="Analysis" value={formatTime(run?.analyzed_at)} />
      </section>

      <section className="levels" aria-label="Key levels">
        <Level label="Spot" value={run?.spot_price} />
        <Level label="Zero Gamma Proxy" value={run?.zero_gamma_proxy} />
        <Level label="Call Wall Proxy" value={run?.call_wall_proxy} />
        <Level label="Put Wall Proxy" value={run?.put_wall_proxy} />
        <Level label="Net GEX Proxy" value={run?.net_gex_proxy} compactValue />
      </section>

      <section className="chart-panel" aria-label="GEX proxy chart">
        <ReactECharts option={chartOptions} style={{ height: 420, width: "100%" }} notMerge lazyUpdate />
      </section>

      <section className="lower-grid">
        <div className="table-panel">
          <div className="panel-heading">Strike Rows</div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {strikeColumns.map((column) => (
                    <th key={column.label}>{column.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(run?.rows ?? []).map((row) => (
                  <tr key={row.strike}>
                    {strikeColumns.map((column) => (
                      <td key={column.label} className={column.className}>
                        {column.render(row)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="side-stack">
          <div className="history-panel">
            <div className="panel-heading">History Replay</div>
            <div className="history-controls">
              <input
                aria-label="History date"
                type="date"
                value={historyDate}
                onChange={(event) => setHistoryDate(event.target.value)}
              />
              <button onClick={handleLoadHistory} disabled={busy} type="button">
                <History size={16} aria-hidden="true" />
                Load
              </button>
            </div>
            <div className="history-list">
              {historyRuns.length === 0 ? (
                <div className="empty">No runs loaded</div>
              ) : (
                historyRuns.map((item) => (
                  <button key={item.id} onClick={() => handleLoadRun(item.id)} type="button">
                    <span className="history-run-main">
                      <span>#{item.id}</span>
                      <strong>{formatTime(item.analyzed_at)}</strong>
                    </span>
                    <span className="history-run-metrics">
                      {historyRunSummaryItems(item, compact).map((metric) => (
                        <span key={metric.label}>
                          {metric.label} <em>{metric.value}</em>
                        </span>
                      ))}
                    </span>
                  </button>
                ))
              )}
            </div>
          </div>

          <div className="warnings-panel">
            <div className="panel-heading">Warnings & Model</div>
            <dl>
              {modelInputItems(run).map((item) => (
                <div key={item.label}>
                  <dt>{item.label}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>
            <ul>
              {(run?.warnings.length ? run.warnings : ["No warnings"]).map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        </div>
      </section>
    </main>
  );
}

function StatusItem({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="status-item">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Level({ label, value, compactValue = false }: { label: string; value?: number | null; compactValue?: boolean }) {
  return (
    <div className="level">
      <span>{label}</span>
      <strong>{value == null ? "n/a" : compactValue ? compact(value) : value.toFixed(2)}</strong>
    </div>
  );
}

function compact(value: number) {
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toFixed(0);
}

function formatTime(value?: string | null) {
  if (!value) return "unknown";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    month: "short",
    day: "2-digit"
  }).format(new Date(value));
}

function formatMarketStatus(status?: MarketStatus | null) {
  if (!status) return "unknown";
  if (status.is_underlying_market_open) return "Underlying open";
  if (status.is_option_market_open) return "Options open";
  if (!status.is_trading_day) return "Closed";
  return "Session closed";
}

function unique(value: string, index: number, array: string[]) {
  return array.indexOf(value) === index;
}

function upsertTicker(tickers: AppTicker[], ticker: AppTicker) {
  const withoutExisting = tickers.filter((item) => item.ticker !== ticker.ticker);
  return [...withoutExisting, ticker].sort((left, right) => left.ticker.localeCompare(right.ticker));
}
