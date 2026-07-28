# Deployment Notes

## Services

Docker Compose runs four services:

- `postgres`: stores the underlying ticker registry, chain captures, spot observations, analysis runs, optional strike rows, and scheduler locks.
- `api`: FastAPI API process only.
- `scheduler`: independent refresh worker.
- `web`: Next.js dashboard.

The scheduler is intentionally separate from the API so API restarts do not duplicate scheduled jobs. In PostgreSQL mode, scheduler jobs acquire bucketed leases in `scheduler_locks`; chain-capture lock keys include ticker, DTE range, max-expiration limit, and the chain-refresh bucket. High-frequency analysis writes also use bucketed `analysis_runs.idempotency_key` values so a retry in the same refresh interval returns the existing run before fetching spot instead of creating a duplicate analysis row.

### Connection pool and process isolation

The API and scheduler are separate processes and each own an independent `psycopg_pool.ConnectionPool`. Every repository operation, and every scheduler lease attempt, checks out its own connection for one unit of work; no long-lived connection is shared across request threads. Multi-statement operations run inside a single `connection.transaction()` on that one connection so transaction boundaries are preserved. Pool sizing is configured with `DB_POOL_MIN_SIZE` (default 1), `DB_POOL_MAX_SIZE` (default 5), and `DB_POOL_TIMEOUT_SECONDS` (default 10). Each pool is opened at startup and closed cleanly on shutdown (the scheduler handles `SIGTERM`/`SIGINT`).

Scheduler lease acquisition is a single atomic `INSERT ... ON CONFLICT (lock_key) DO UPDATE ... WHERE (lease expired OR same owner) RETURNING owner_id`, so two owners contending for a brand-new lock key cannot both win — the caller acquired the lease only if the returned owner is itself.

### Exposure model

Compose binds the `api` and `web` published ports to `${GEXBOT_BIND_HOST:-127.0.0.1}` (localhost by default) and does not publish PostgreSQL to the host at all — it is reachable only on the internal Docker network. Change `GEXBOT_BIND_HOST` deliberately for LAN/VPN/reverse-proxy access, and only expose beyond localhost behind authentication. An optional `GEXBOT_API_KEY` gates mutating endpoints when set; the bundled browser UI does not send it, so it is intended for API-only/reverse-proxy use. See the README "Security & Exposure Model" section.

Compose initializes the database from `backend/app/db/schema.sql`. Runtime schema initialization is disabled in Compose via `AUTO_ENSURE_SCHEMA=false` to avoid startup-time DDL lock waits. Boolean settings such as `AUTO_ENSURE_SCHEMA` accept `true`, `false`, `1`, `0`, `yes`, `no`, `on`, or `off`; any other value fails startup.

`GEXBOT_TICKERS` seeds enabled rows in `underlyings` when the API or scheduler starts. Custom tickers added with `POST /api/v1/tickers` or the dashboard ticker input are persisted in the same table, and the scheduler reads enabled tickers from that registry on each loop. `PATCH /api/v1/tickers/{ticker}` updates `enabled` and/or `dividend_yield`; disabling a ticker from the API or dashboard preserves its historical rows while removing it from API ticker lists and future scheduler loops. API `dividend_yield` values are decimal fractions from 0 to 1; the dashboard `Div %` field accepts percent input and converts it before saving.

Ticker inputs are normalized at configuration, API, and runtime boundaries: surrounding whitespace is stripped, symbols are uppercased, and only 1-12 characters using letters, digits, dot, or hyphen are accepted. Invalid API ticker inputs return `422` before reaching the market data provider; invalid `GEXBOT_TICKERS` values fail startup.

## Provider Configuration

Default local mode uses mock data:

```bash
GEXBOT_PROVIDER=mock
```

`GEXBOT_PROVIDER` is validated at startup and only accepts `mock` or `tradier`; unsupported values fail fast instead of falling back to mock data. `TRADIER_TOKEN` is required when `GEXBOT_PROVIDER=tradier`, and an empty or whitespace-only token fails startup.

Tradier mode:

```bash
GEXBOT_PROVIDER=tradier
TRADIER_TOKEN=...
TRADIER_BASE_URL=https://api.tradier.com/v1
```

Live provider smoke test:

```bash
cd backend
GEXBOT_PROVIDER=tradier TRADIER_TOKEN=... .venv/bin/python -m app.providers.smoke --ticker SPY --max-expirations 1
```

The smoke test prints a JSON summary with spot, selected expirations, contract count, and booleans for IV, open interest, volume, and contract multiplier availability. It does not print the token or raw option-chain payload.

The smoke command exits non-zero when the sampled provider data is missing inputs required for GEX proxy calculation: positive spot price, IV, open interest, and contract multiplier. Volume availability is reported for display/data-quality review but is not required for GEX proxy math.
The smoke ticker is validated locally with the same symbol rules as the API and scheduler, so obviously invalid symbols fail before any Tradier request is sent.
Dot class tickers are stored and displayed in normalized app form, such as `BRK.B`, but the Tradier provider converts them to slash market-data symbols such as `BRK/B` for quote, expiration, and chain requests.

The Tradier adapter calls:

- `/markets/options/expirations`
- `/markets/options/chains`
- `/markets/quotes`

HTTP calls use a small reliability layer:

- successful GET JSON responses are cached in-process by path and query until `CHAIN_REFRESH_SECONDS` elapses;
- transient network/5xx failures retry up to 3 attempts;
- HTTP 429 returns a structured provider-rate-limit error;
- IV/OI timestamps, spot observed timestamps, and contract multipliers remain `null` if the provider does not supply them; provider receive timestamps are stored and displayed separately. Tradier quote `trade_date` is stored as `spot_observed_at` when present on a live last-price quote, and remains `null` for close-price fallback. Missing contract multipliers are excluded from GEX proxy calculations with `missing_multiplier_excluded` instead of being silently treated as 100.
- malformed Tradier expiration, option-chain, quote, Greeks, and numeric payload fields are reported as provider data errors; non-positive IV, strike, or contract multiplier values are excluded from GEX proxy calculations with explicit warnings instead of causing a calculation crash. If every captured contract is excluded, analysis warnings include `all_contracts_excluded` so a zero net GEX proxy is not mistaken for a fully populated calculation.
- vendor Greeks and quote fields are preserved in chain snapshots when supplied, while GEX proxy uses the local model inputs for recalculation.

The dashboard parses structured API errors and displays provider error codes, messages, provider HTTP status codes, rate-limit fields, and validation field paths instead of only showing the HTTP status code. API request validation failures return `error.code=validation_error` with a readable message and `fields` array.

## Refresh Policy

Default scheduler cadence:

- full or partial chain capture every `CHAIN_REFRESH_SECONDS`, default 900 seconds;
- expiration range filtering uses the New York market date at capture time; invalid negative or inverted DTE ranges are rejected, and explicit `expirations` requests are still constrained by the same DTE range and max-expiration limit;
- `CHAIN_REFRESH_SECONDS`, `SPOT_REFRESH_SECONDS`, and `DEFAULT_MAX_EXPIRATIONS` must be positive; `DEFAULT_MIN_DTE` and `DEFAULT_MAX_DTE` must be non-negative and ordered at startup; `DEFAULT_RISK_FREE_RATE` must be finite and `DEFAULT_DIVIDEND_YIELD` must be a finite 0-1 decimal fraction;
- chain-capture scheduler locks are bucketed by `CHAIN_REFRESH_SECONDS`, ticker, DTE range, and max-expiration limit;
- market sessions use a versioned NYSE holiday and early-close calendar stored on analysis runs;
- one spot observation per ticker per spot-refresh bucket, reused for local GEX proxy analysis of both `all` and `0dte` scopes every `SPOT_REFRESH_SECONDS`, default 15 seconds;
- scheduler spot analysis is existing-capture-only and idempotent per ticker/scope/spot-refresh bucket: it looks up an existing bucketed run before fetching spot, never performs an implicit option-chain capture in the high-frequency path, and logs a provider error until the low-frequency chain job has stored a usable capture;
- `GET /api/v1/gex-proxy/latest` is read-only and returns `404 no_latest_run` when no stored analysis exists; it does not capture chains, fetch spot, or create analysis rows;
- `POST /api/v1/gex-proxy/analyze` is existing-capture-only by default; use `allow_chain_capture=true` only for an explicit bootstrap path that may perform the first chain capture;
- `GET /api/v1/chains/latest` is read-only and returns `404 no_latest_chain_capture` until a successful or partial capture exists;
- spot analysis runs during the option session, from the session open until `option_last_trade_at`; for SPY/QQQ this keeps high-frequency spot/local GEX proxy recalculation active during the 16:00-16:15 ET ETF option window;
- option chain captures run during the option session, from the session open until `option_last_trade_at`, including the SPY/QQQ late option window;
- failed scheduler attempts are logged and throttled by the same local interval as successful attempts, so a provider outage does not collapse into one request per scheduler loop;
- after option last trade, scheduled jobs pause;
- enabled tickers are read from the persisted registry rather than only from the process environment.

`GET /api/v1/market/status?ticker=...` exposes the New York `market_date` plus both session states. During the 16:00-16:15 ET SPY/QQQ ETF option window on a full day, `is_underlying_market_open=false` and `is_option_market_open=true`; standard single-name tickers use the 16:00 option policy.

GEX proxy expiry filtering and Black-Scholes time-to-expiry use `option_last_trade_at`. The separate `underlying_close_at` is kept for market-session display, scope dating, and audit context.

Calendar version `nyse_rules_v3` covers the published NYSE 2025-2028 holiday/early-close schedule and the January 9, 2025 National Day of Mourning full closure. Review this calendar when NYSE publishes new yearly schedules or one-off closures.

The dashboard defaults history replay to the New York market date, reads the status endpoint on ticker changes, polls it every 60 seconds outside the option session, and uses `spot_refresh_seconds` for automatic latest-analysis polling while the option session is open. For SPY/QQQ this keeps the dashboard polling latest runs during the 16:00-16:15 ET ETF option window. Outside that window the current run remains visible and manual refresh controls still call the API. IV and OI timestamp cards include known/total coverage so missing provider observation times remain visible during data QA. History replay rows include net GEX proxy, zero gamma proxy, call wall proxy, and put wall proxy before loading a full run.

## Runtime Data

High-frequency spot analysis persists lightweight `analysis_runs`. Full strike rows are returned on demand for latest/run-detail API responses and are persisted to `strike_gex_proxy` only when `POST /api/v1/gex-proxy/runs/{run_id}/persist-rows` or the dashboard checkpoint button is used.

History replay filters `analyzed_at` by New York market date inside PostgreSQL before applying the response limit, so a high-frequency intraday run set is not truncated before date filtering. The API defaults history responses to 2000 runs and accepts `limit=1..5000`; the dashboard requests 2000 runs, enough for a standard 15-second full trading session per scope.

Option-chain capture responses stay compact by default. Use `POST /api/v1/chains/capture?include_contracts=true` when you need to inspect raw contract inputs for QA or debugging; this includes OI, volume, IV, contract multiplier, quotes, vendor Greeks, and exact underlying/option close timestamps. Use `GET /api/v1/chains/latest?include_contracts=true` to inspect the latest usable capture without triggering another provider call.

If the first option-chain capture for a ticker fails for every requested expiration, analysis endpoints return a structured provider error instead of emitting a zero-valued GEX proxy result. A DTE range, including after applying explicit `expirations`, that selects no expirations returns `capture_failed` with `no_expirations_selected` and is not treated as a usable empty success. A selected expiration that returns zero contracts is treated as a failed expiration, so mixed empty/non-empty chain responses become partial captures and all-empty captures return `capture_failed`. Partial captures remain analyzable and carry warnings.

Historical replay loads the stored summary metrics from `analysis_runs` so past net GEX proxy, zero gamma proxy, call wall proxy, put wall proxy, excluded count, and warnings remain the values recorded at analysis time. If strike rows were checkpointed, replay uses the stored rows; otherwise row detail is rebuilt from stored inputs for inspection. Historical recompute uses stored chain inputs, spot price, nullable spot observed time, spot provider received time, model version, calendar version, close-policy version, rates, dividend yield, multiplier, and position assumption. Analysis responses include `chain_capture_id`, making it explicit when multiple spot recalculations share the same low-frequency chain snapshot. The recompute endpoint returns original/recomputed/diff values for net GEX proxy, zero gamma proxy, call wall proxy, and put wall proxy so replay checks cover all key levels. Recompute warnings preserve original run context such as `partial_capture` and `analysis_uses_partial_capture` in addition to warnings emitted by the fresh model calculation.

Historical recompute is model-version gated. Version `black_scholes_v1.0.0` is currently supported; an unknown stored `model_version` returns `409 unsupported_model_version` instead of silently recomputing with the current formula.
