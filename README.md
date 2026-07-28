# GEX Proxy Bot

Self-hosted dashboard for SPY, QQQ, and custom ticker GEX proxy analysis.

The dashboard reports **GEX proxy** values under a documented `call-positive / put-negative` position assumption. It is not a claim about actual dealer inventory.

## What Is Implemented

- Python FastAPI backend.
- Separate scheduler process.
- PostgreSQL schema for underlying ticker registry, chain captures, expiration captures, contract captures, spot observations, analysis runs, optional strike rows, and scheduler locks.
- Mock provider for local development.
- Tradier provider adapter for option expirations, option chains, and spot quotes.
- Option-chain snapshots preserve OI, volume, IV, contract multiplier, quote fields, and vendor Greeks when supplied.
- Local Black-Scholes gamma and GEX proxy calculation.
- Expiration-limited partial chain capture.
- Separate `underlying_close_at` and `option_last_trade_at`.
- Versioned NYSE holiday and early-close calendar with SPY/QQQ late option last-trade policy.
- 0DTE and all-expiration analysis scopes.
- Next.js dashboard with chart, key levels, timestamps, warnings, and refresh controls.
- Dashboard market-status display and auto-refresh cadence driven by `/api/v1/market/status`.
- Persistent custom ticker registry shared by API and scheduler.
- Explicit replay checkpoints that persist strike-level rows on demand.
- Market status endpoint reports separate underlying and option session state; SPY/QQQ options remain open until the ETF option last-trade timestamp while the underlying session closes at 16:00 ET.

## Run

```bash
cp .env.example .env
docker compose up --build
```

Open:

- Web: http://localhost:3000
- API: http://localhost:8000/health

## Tradier

Set:

```bash
GEXBOT_PROVIDER=tradier
TRADIER_TOKEN=your_token
```

`GEXBOT_PROVIDER` is validated at startup and only accepts `mock` or `tradier`; typos fail fast instead of silently running mock data. `TRADIER_TOKEN` is required when `GEXBOT_PROVIDER=tradier`, and an empty or whitespace-only token fails startup.

The system uses low-frequency chain capture and high-frequency spot recalculation. During scheduled refreshes, each ticker fetches one spot quote per spot-refresh bucket and reuses that observation for both `all` and `0dte` local recalculations. Spot/local GEX proxy analysis runs until the ticker's `option_last_trade_at`, so SPY/QQQ continue through the 16:00-16:15 ET ETF option window. Tradier quote `trade_date` is stored as `spot_observed_at` when present on a live last-price quote. If Tradier does not provide IV/OI, contract multiplier, or spot observation time, those fields remain `null`; provider receive time is tracked and displayed separately. If every captured contract is excluded from GEX proxy math because required inputs are missing, invalid, or expired, analysis warnings include `all_contracts_excluded`.

The scheduler's high-frequency spot path is existing-capture-only. It recalculates from the latest successful or partial chain capture and does not perform an implicit option-chain request every spot interval.

Scheduler spot analysis is idempotent per ticker, scope, and spot-refresh time bucket. Retries in the same bucket look up the existing analysis run before fetching spot and reuse it instead of appending duplicate `analysis_runs` rows.

`GET /api/v1/gex-proxy/latest` is read-only. It returns `404 no_latest_run` until an analysis run exists. `POST /api/v1/gex-proxy/analyze` is also existing-capture-only by default; pass `allow_chain_capture=true` only for an explicit bootstrap analysis that may create the initial chain capture.

`GEXBOT_TICKERS` seeds the `underlyings` registry at startup. Environment and API ticker inputs are trimmed, uppercased, validated as 1-12 characters using letters, digits, dot, or hyphen, persisted there, and picked up by the independent scheduler. Invalid configured tickers fail startup instead of reaching the market data provider during refresh. For Tradier market-data requests, dot class symbols such as `BRK.B` are converted to Tradier's slash form `BRK/B` at the provider boundary while the app keeps `BRK.B` internally.

`POST /api/v1/tickers` adds or re-enables a ticker. `PATCH /api/v1/tickers/{ticker}` accepts `enabled` and/or `dividend_yield`; setting `enabled=false` keeps the registry row but removes that ticker from `GET /api/v1/tickers` and from scheduler refresh loops. The dashboard exposes this for custom tickers through the ticker-strip delete icon, and its `Div %` input is converted to the 0-1 `dividend_yield` value used by zero-gamma proxy calculations.

High-frequency spot recalculations stay lightweight by default. Historical replay keeps the stored summary metrics from each analysis run; use `POST /api/v1/gex-proxy/runs/{run_id}/persist-rows` or the dashboard checkpoint button to also persist strike-level rows for replay.

`POST /api/v1/chains/capture?include_contracts=true` returns inspectable option-chain contract details for data QA, including OI, volume, IV, contract multiplier, quote fields, vendor Greeks, and exact underlying/option close timestamps. The default capture response omits contract rows to keep manual refresh payloads small.

DTE filtering applies to both automatic expiration selection and explicit `expirations` requests; explicit dates outside the configured/requested DTE window are skipped. A selection that leaves no expirations is treated as a failed capture with `no_expirations_selected`; it is not stored as a usable empty success.

Refresh cadence, DTE, model input, and boolean configuration are validated at startup: `CHAIN_REFRESH_SECONDS`, `SPOT_REFRESH_SECONDS`, and `DEFAULT_MAX_EXPIRATIONS` must be positive; `DEFAULT_MIN_DTE` and `DEFAULT_MAX_DTE` must be non-negative and ordered; `DEFAULT_RISK_FREE_RATE` must be finite; `DEFAULT_DIVIDEND_YIELD` must be a finite 0-1 decimal fraction; boolean settings such as `AUTO_ENSURE_SCHEMA` must be one of `true`, `false`, `1`, `0`, `yes`, `no`, `on`, or `off`.

`GET /api/v1/chains/latest?ticker=SPY` is read-only and returns the latest successful or partial chain capture without contacting the provider. Analysis responses include `chain_capture_id`, so multiple high-frequency spot recalculations can be tied back to the same low-frequency chain snapshot during replay.

`GET /api/v1/market/status?ticker=SPY` exposes the New York `market_date`, `underlying_open_at`, `underlying_close_at`, `option_last_trade_at`, `is_underlying_market_open`, and `is_option_market_open`. For SPY/QQQ, `option_last_trade_at` is 15 minutes after the underlying close, including early-close days.

GEX proxy calculations exclude contracts only after `option_last_trade_at`; they do not clamp expired same-day contracts to a tiny positive time.

The dashboard defaults history replay to the New York market date, polls market status every 60 seconds outside the option session so a page left open before the bell can detect the open, and refreshes latest analysis using `spot_refresh_seconds` while `is_option_market_open=true`. Manual spot/chain refresh buttons remain available outside the automatic latest-analysis polling window. IV and OI timestamp cards show known/total coverage so provider timestamp gaps are visible instead of being hidden. History replay rows show net GEX proxy, zero gamma proxy, call wall proxy, and put wall proxy for fast intraday comparison.

Before running Tradier in the scheduler, smoke-test the live provider without persisting data:

```bash
cd backend
GEXBOT_PROVIDER=tradier TRADIER_TOKEN=your_token .venv/bin/python -m app.providers.smoke --ticker SPY --max-expirations 1
```

The smoke command exits non-zero if Tradier provides no positive spot price or if IV, open interest, or contract multiplier are completely absent from the sampled contracts.
The smoke ticker uses the same local validation as the API and scheduler, so invalid symbols fail before any provider request is sent.

## Backend Tests

```bash
cd backend
.venv/bin/python -m unittest discover -s tests
```

See [docs/testing.md](docs/testing.md) for the full verification gate and [docs/deployment.md](docs/deployment.md) for Docker and Tradier deployment notes.
