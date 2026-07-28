export type Scope = "all" | "0dte";

export type GexRow = {
  strike: number;
  call_gex_proxy: number;
  put_gex_proxy: number;
  net_gex_proxy: number;
  call_oi: number;
  put_oi: number;
  call_volume: number;
  put_volume: number;
};

export type ObservedSummary = {
  min: string | null;
  max: string | null;
  known_count: number;
  unknown_count: number;
};

export type GexRun = {
  id: number;
  chain_capture_id: number;
  metric_name: "gex_proxy";
  ticker: string;
  scope: Scope;
  position_assumption: string;
  pricing_model: string;
  model_version: string;
  calendar_version: string;
  option_close_policy_version: string;
  risk_free_rate: number;
  risk_free_rate_source: string;
  dividend_yield: number;
  dividend_yield_source: string;
  spot_price: number;
  spot_observed_at: string | null;
  spot_provider_received_at: string | null;
  chain_status: "success" | "partial" | "failed";
  chain_min_dte: number;
  chain_max_dte: number;
  requested_expirations: string[];
  included_expirations: string[];
  chain_captured_at: string;
  analyzed_at: string;
  net_gex_proxy: number;
  zero_gamma_proxy: number | null;
  call_wall_proxy: number | null;
  put_wall_proxy: number | null;
  excluded_contract_count: number;
  persist_strike_rows: boolean;
  iv_time_summary: ObservedSummary;
  oi_time_summary: ObservedSummary;
  warnings: string[];
  rows?: GexRow[];
};

export type AppTicker = {
  ticker: string;
  dividend_yield: number;
  enabled: boolean;
};

export type MarketStatus = {
  ticker: string;
  calendar_version: string;
  market_date: string;
  is_trading_day: boolean;
  is_half_day: boolean;
  is_underlying_market_open: boolean;
  is_option_market_open: boolean;
  underlying_open_at: string | null;
  underlying_close_at: string | null;
  option_last_trade_at: string | null;
  option_last_trade_policy: string;
  chain_refresh_seconds: number;
  spot_refresh_seconds: number;
};

export const CLOSED_MARKET_STATUS_REFRESH_SECONDS = 60;

export function dashboardRefreshIntervalMs(status: MarketStatus | null): number {
  if (!status?.is_option_market_open) {
    return CLOSED_MARKET_STATUS_REFRESH_SECONDS * 1000;
  }
  return Math.max(5, status.spot_refresh_seconds) * 1000;
}

export function nyMarketDateInputValue(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type ApiErrorDetails = {
  code?: string;
  message?: string;
  provider_status_code?: number;
  rate_limit_available?: string;
  rate_limit_expiry?: string;
  warnings?: string[];
  fields?: ApiErrorField[];
};

type ApiErrorField = {
  path: string;
  message: string;
};

export class ApiRequestError extends Error {
  readonly operation: string;
  readonly status: number;
  readonly code?: string;
  readonly providerStatusCode?: number;
  readonly rateLimitAvailable?: string;
  readonly rateLimitExpiry?: string;
  readonly warnings?: string[];
  readonly fields?: ApiErrorField[];

  constructor(operation: string, status: number, details: ApiErrorDetails | null, fallbackBody = "") {
    super(formatApiError(operation, status, details, fallbackBody));
    this.name = "ApiRequestError";
    this.operation = operation;
    this.status = status;
    this.code = details?.code;
    this.providerStatusCode = details?.provider_status_code;
    this.rateLimitAvailable = details?.rate_limit_available;
    this.rateLimitExpiry = details?.rate_limit_expiry;
    this.warnings = details?.warnings;
    this.fields = details?.fields;
  }
}

export function isNoLatestRunError(error: unknown): error is ApiRequestError & { code: "no_latest_run" } {
  return error instanceof ApiRequestError && error.code === "no_latest_run";
}

export async function fetchTickers(): Promise<{ tickers: AppTicker[] }> {
  const response = await fetch(`${API_BASE}/api/v1/tickers`, { cache: "no-store" });
  if (!response.ok) {
    throw await apiRequestError("tickers", response);
  }
  return response.json();
}

export async function fetchMarketStatus(ticker: string): Promise<MarketStatus> {
  const params = new URLSearchParams({ ticker });
  const response = await fetch(`${API_BASE}/api/v1/market/status?${params}`, { cache: "no-store" });
  if (!response.ok) {
    throw await apiRequestError("market status", response);
  }
  return response.json();
}

export async function addTicker(ticker: string, dividendYield?: number): Promise<AppTicker> {
  const response = await fetch(`${API_BASE}/api/v1/tickers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker, dividend_yield: dividendYield })
  });
  if (!response.ok) {
    throw await apiRequestError("add ticker", response);
  }
  return response.json();
}

type TickerUpdateOptions = {
  enabled?: boolean;
  dividendYield?: number;
};

export function buildTickerUpdateRequestBody(options: TickerUpdateOptions): { enabled?: boolean; dividend_yield?: number } {
  return {
    ...(options.enabled !== undefined ? { enabled: options.enabled } : {}),
    ...(options.dividendYield !== undefined ? { dividend_yield: options.dividendYield } : {})
  };
}

export async function updateTicker(ticker: string, options: TickerUpdateOptions): Promise<AppTicker> {
  const response = await fetch(`${API_BASE}/api/v1/tickers/${encodeURIComponent(ticker)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildTickerUpdateRequestBody(options))
  });
  if (!response.ok) {
    throw await apiRequestError("update ticker", response);
  }
  return response.json();
}

export async function fetchLatest(ticker: string, scope: Scope, includeRows = true): Promise<GexRun> {
  const params = new URLSearchParams({
    ticker,
    scope,
    include_rows: String(includeRows)
  });
  const response = await fetch(`${API_BASE}/api/v1/gex-proxy/latest?${params}`, { cache: "no-store" });
  if (!response.ok) {
    throw await apiRequestError("latest", response);
  }
  return response.json();
}

type AnalyzeOptions = {
  allowChainCapture?: boolean;
};

export function buildAnalyzeRequestBody(
  ticker: string,
  scope: Scope,
  options?: { allowChainCapture?: false }
): {
  ticker: string;
  scope: Scope;
  persist_strike_rows: false;
  allow_chain_capture: false;
};
export function buildAnalyzeRequestBody(
  ticker: string,
  scope: Scope,
  options: { allowChainCapture: true }
): {
  ticker: string;
  scope: Scope;
  persist_strike_rows: false;
  allow_chain_capture: true;
};
export function buildAnalyzeRequestBody(
  ticker: string,
  scope: Scope,
  options: AnalyzeOptions
): {
  ticker: string;
  scope: Scope;
  persist_strike_rows: false;
  allow_chain_capture: boolean;
};
export function buildAnalyzeRequestBody(ticker: string, scope: Scope, options: AnalyzeOptions = {}) {
  return {
    ticker,
    scope,
    persist_strike_rows: false,
    allow_chain_capture: options.allowChainCapture === true
  };
}

export async function analyzeNow(ticker: string, scope: Scope, options: AnalyzeOptions = {}): Promise<GexRun> {
  const response = await fetch(`${API_BASE}/api/v1/gex-proxy/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildAnalyzeRequestBody(ticker, scope, options))
  });
  if (!response.ok) {
    throw await apiRequestError("analyze", response);
  }
  return response.json();
}

export async function captureChain(ticker: string, minDte: number, maxDte: number) {
  const response = await fetch(`${API_BASE}/api/v1/chains/capture`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker, min_dte: minDte, max_dte: maxDte })
  });
  if (!response.ok) {
    throw await apiRequestError("capture", response);
  }
  return response.json();
}

export async function fetchHistory(ticker: string, scope: Scope, date: string, limit = 2000): Promise<{ runs: GexRun[] }> {
  const params = new URLSearchParams({ ticker, scope, date, limit: String(limit) });
  const response = await fetch(`${API_BASE}/api/v1/gex-proxy/history?${params}`, { cache: "no-store" });
  if (!response.ok) {
    throw await apiRequestError("history", response);
  }
  return response.json();
}

export async function fetchRun(runId: number, includeRows = true): Promise<GexRun> {
  const params = new URLSearchParams({ include_rows: String(includeRows) });
  const response = await fetch(`${API_BASE}/api/v1/gex-proxy/runs/${runId}?${params}`, { cache: "no-store" });
  if (!response.ok) {
    throw await apiRequestError("run", response);
  }
  return response.json();
}

export async function persistRunRows(runId: number): Promise<GexRun> {
  const response = await fetch(`${API_BASE}/api/v1/gex-proxy/runs/${runId}/persist-rows`, {
    method: "POST"
  });
  if (!response.ok) {
    throw await apiRequestError("checkpoint", response);
  }
  return response.json();
}

export async function parseApiError(operation: string, response: Response): Promise<string> {
  const bodyText = await response.text();
  const structured = parseErrorBody(bodyText);
  return formatApiError(operation, response.status, structured, bodyText);
}

async function apiRequestError(operation: string, response: Response): Promise<ApiRequestError> {
  const bodyText = await response.text();
  const structured = parseErrorBody(bodyText);
  return new ApiRequestError(operation, response.status, structured, bodyText);
}

function formatApiError(
  operation: string,
  status: number,
  structured: ParsedErrorBody | null,
  bodyText: string,
): string {
  if (!structured) {
    return `${operation} request failed: ${status}${bodyText ? ` ${bodyText}` : ""}`;
  }

  const parts = [`${operation} request failed: ${status}`];
  if (structured.code) {
    parts.push(structured.code);
  }
  if (structured.message) {
    parts.push(structured.message);
  } else if (structured.fields?.length) {
    parts.push(formatErrorFields(structured.fields));
  }
  if (structured.provider_status_code !== undefined) {
    parts.push(`provider_status_code=${structured.provider_status_code}`);
  }
  if (structured.rate_limit_available || structured.rate_limit_expiry) {
    parts.push(
      `rate_limit_available=${structured.rate_limit_available ?? "unknown"} rate_limit_expiry=${
        structured.rate_limit_expiry ?? "unknown"
      }`
    );
  }
  if (structured.warnings?.length) {
    parts.push(`warnings=${structured.warnings.join(",")}`);
  }
  return parts.join(": ");
}

type ParsedErrorBody = ApiErrorDetails;

function parseErrorBody(bodyText: string): ParsedErrorBody | null {
  if (!bodyText) {
    return null;
  }
  try {
    const decoded = JSON.parse(bodyText) as unknown;
    if (!decoded || typeof decoded !== "object") {
      return null;
    }

    const maybeFastApiError = parseFastApiDetail((decoded as { detail?: unknown }).detail);
    if (maybeFastApiError) {
      return maybeFastApiError;
    }

    if (!("error" in decoded)) {
      return null;
    }

    const error = (decoded as { error?: unknown }).error;
    if (!error || typeof error !== "object") {
      return null;
    }
    const value = error as Record<string, unknown>;
    return {
      code: typeof value.code === "string" ? value.code : undefined,
      message: typeof value.message === "string" ? value.message : undefined,
      provider_status_code: typeof value.provider_status_code === "number" ? value.provider_status_code : undefined,
      rate_limit_available: typeof value.rate_limit_available === "string" ? value.rate_limit_available : undefined,
      rate_limit_expiry: typeof value.rate_limit_expiry === "string" ? value.rate_limit_expiry : undefined,
      warnings: Array.isArray(value.warnings) ? value.warnings.filter((item): item is string => typeof item === "string") : undefined,
      fields: parseErrorFields(value.fields)
    };
  } catch {
    return null;
  }
}

function parseErrorFields(fields: unknown): ApiErrorField[] | undefined {
  if (!Array.isArray(fields)) {
    return undefined;
  }

  const parsed = fields.flatMap((item) => {
    if (!item || typeof item !== "object") {
      return [];
    }
    const value = item as Record<string, unknown>;
    if (typeof value.path !== "string" || typeof value.message !== "string") {
      return [];
    }
    return [{ path: value.path, message: value.message }];
  });

  return parsed.length > 0 ? parsed : undefined;
}

function formatErrorFields(fields: ApiErrorField[]): string {
  return fields.map((field) => `${field.path}: ${field.message}`).join("; ");
}

function parseFastApiDetail(detail: unknown): ParsedErrorBody | null {
  if (typeof detail === "string") {
    return { code: "request_error", message: detail };
  }

  if (!Array.isArray(detail)) {
    return null;
  }

  const messages = detail.flatMap((item) => {
    if (!item || typeof item !== "object") {
      return [];
    }

    const value = item as Record<string, unknown>;
    if (typeof value.msg !== "string") {
      return [];
    }

    const path = Array.isArray(value.loc)
      ? value.loc
          .filter((part): part is string | number => typeof part === "string" || typeof part === "number")
          .map(String)
          .join(".")
      : "";

    return path ? [`${path}: ${value.msg}`] : [value.msg];
  });

  return messages.length > 0 ? { code: "validation_error", message: messages.join("; ") } : null;
}
