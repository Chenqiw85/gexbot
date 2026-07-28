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

## Direct-Use Setup

This is a single-user, private application. By default every published port binds
to `127.0.0.1` only, so the stack is reachable from your machine but not the LAN
or the public internet. See [Security & Exposure Model](#security--exposure-model)
before changing that.

### 1. Configure

```bash
cp .env.example .env
```

Edit `.env`. For a first run you can keep the defaults (mock provider). To use
real data, set:

```bash
GEXBOT_PROVIDER=tradier
TRADIER_TOKEN=your_token          # required for tradier; never commit it
TRADIER_BASE_URL=https://api.tradier.com/v1   # or the sandbox URL
```

### 2. Start

```bash
docker compose up --build -d
```

This starts `postgres` (internal only), `api`, `scheduler`, and `web`.

### 3. Open the dashboard

- Dashboard (web UI): **http://localhost:3000**
- API liveness: http://localhost:8000/health
- API readiness (checks DB + provider config, no provider calls): http://localhost:8000/ready

### 4. Use it

`SPY` and `QQQ` are seeded and enabled at startup. From the dashboard you can
register/enable another ticker, or via the API:

```bash
API=http://127.0.0.1:8000

# 5. Register or enable a ticker (SPY/QQQ already seeded)
curl -X POST $API/api/v1/tickers -H 'Content-Type: application/json' -d '{"ticker":"SPY"}'

# 6. Trigger a capture
curl -X POST $API/api/v1/chains/capture -H 'Content-Type: application/json' \
  -d '{"ticker":"SPY","min_dte":0,"max_dte":7}'

# 7. Confirm a GEX proxy analysis run was created
curl -X POST $API/api/v1/gex-proxy/analyze -H 'Content-Type: application/json' \
  -d '{"ticker":"SPY","scope":"all","persist_strike_rows":false}'
# -> returns a run with id, net_gex_proxy, zero_gamma_proxy, call/put wall proxies

# 8. Verify scheduler status and recent captures
docker compose logs --tail=50 scheduler
curl "$API/api/v1/gex-proxy/latest?ticker=SPY&scope=all"
```

> Outside NYSE trading hours the scheduler intentionally does not capture or
> analyze (see [Expected behavior outside market hours](#expected-behavior-outside-market-hours)).
> Use the manual `capture`/`analyze` calls above (with `allow_chain_capture=true`
> for the first bootstrap analysis) to exercise the pipeline any time — in mock
> mode this always works.

## Provider Modes

`GEXBOT_PROVIDER` is validated at startup and only accepts `mock` or `tradier`;
typos fail fast instead of silently running mock data.

- **Mock mode** (`GEXBOT_PROVIDER=mock`, the default): synthetic option data for
  local development and demos. No token required. Works any time of day.
- **Tradier mode** (`GEXBOT_PROVIDER=tradier`): real market data. `TRADIER_TOKEN`
  is **required**; an empty or whitespace-only token fails startup with a clear
  error and the token is never logged. Provider/config errors surface through the
  structured API error envelope. Before enabling Tradier in the scheduler, run the
  live smoke test (below).

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

## Security & Exposure Model

This app has **no user accounts** and is designed for private, single-user
self-hosting. Its primary protection is network binding, not authentication.

- **Localhost-only by default.** Compose publishes the API and web ports on
  `127.0.0.1` (via `GEXBOT_BIND_HOST`, default `127.0.0.1`). PostgreSQL is not
  published to the host at all — it is only reachable on the internal Docker
  network by `api`/`scheduler`.
- **Changing the binding deliberately.** To reach the dashboard from another
  device (LAN, Tailscale/VPN, or behind a reverse proxy), set `GEXBOT_BIND_HOST`
  in `.env`:
  - Tailscale/VPN: bind to your tailnet/VPN interface IP (e.g. `GEXBOT_BIND_HOST=100.x.y.z`).
  - LAN: your host's LAN IP.
  - Reverse proxy on the same host: keep `127.0.0.1` and point the proxy at it.
  - `0.0.0.0` exposes the app on **all** interfaces — do this only behind a
    firewall/VPN/authenticating reverse proxy.
- **Public exposure requires authentication and rate limiting** that this app
  does not provide on its own. Put it behind an authenticating reverse proxy (or
  a VPN) before exposing it beyond localhost. When `GEXBOT_BIND_HOST` is not a
  localhost address and no API key is set, the app logs a warning at startup.
- **Optional API key (`GEXBOT_API_KEY`).** When unset (default), localhost use
  needs no auth. When set, mutating endpoints (`POST`/`PATCH`/`PUT`/`DELETE`)
  require a matching `X-API-Key` header. **Limitation:** the bundled browser
  frontend does not send this header (doing so securely would require a
  server-side proxy, which is out of scope for this single-user build), so
  enabling `GEXBOT_API_KEY` is intended for API-only or reverse-proxy
  deployments, not for use with the bundled web UI. Keep localhost binding as the
  primary protection.
- **Application-level safeguards** apply regardless of the key: capture/analyze
  requests must target a registered, enabled ticker (unknown/disabled tickers are
  rejected with `404 ticker_not_enabled` before any provider call), ticker format
  and length are validated (1–12 chars, letters/digits/dot/hyphen), and DTE
  ranges/payloads are bounded.

## Database, Connection Pool & Backups

- **Connection pool.** The backend uses a `psycopg_pool.ConnectionPool`; each
  repository operation (and each scheduler lease attempt) checks out its own
  connection, so no connection is shared across request threads. Multi-statement
  operations run inside a single `connection.transaction()` on one connection.
  Sizing is configurable:
  - `DB_POOL_MIN_SIZE` (default `1`)
  - `DB_POOL_MAX_SIZE` (default `5`)
  - `DB_POOL_TIMEOUT_SECONDS` (default `10`)
  The API and scheduler are separate processes and each own an independent pool.
  The pool is opened at startup and closed cleanly on shutdown.
- **Data location / backups.** PostgreSQL data lives in the named Docker volume
  `postgres_data`. Back it up with, e.g.:
  ```bash
  docker compose exec -T postgres pg_dump -U gexbot gexbot > gexbot-backup.sql
  # restore:
  docker compose exec -T postgres psql -U gexbot -d gexbot < gexbot-backup.sql
  ```
  `docker compose down` keeps the volume; `docker compose down -v` **deletes** it.

## Operations

```bash
# Stop (keeps data volume)
docker compose stop
# Start again
docker compose up -d
# Full teardown, KEEP data
docker compose down
# Full teardown, DELETE data
docker compose down -v

# Inspect logs
docker compose logs --tail=100 api
docker compose logs --tail=100 scheduler
docker compose logs -f web
```

The scheduler handles `SIGTERM`/`SIGINT` for graceful shutdown (it finishes the
current tick, then closes its pool), so `docker compose stop`/`restart` are safe.
One failed ticker or provider error in a polling cycle is logged and throttled;
it does not terminate the cycle or the scheduler.

## Tests

Backend tests use `unittest`. Create a virtualenv and install the backend
package (which now includes `psycopg_pool`):

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -e . pytest
.venv/bin/python -m unittest discover -s tests      # unit + API + in-memory concurrency
.venv/bin/python -m compileall -q app tests
```

Real-PostgreSQL concurrency tests (connection isolation, same-key idempotency
race, scheduler-lease vs manual-analysis concurrency) are **skipped unless**
`GEXBOT_TEST_DATABASE_URL` is set. To run them against a throwaway database:

```bash
docker run -d --name gexbot-test-pg -e POSTGRES_USER=gexbot \
  -e POSTGRES_PASSWORD=gexbot -e POSTGRES_DB=gexbot -p 55432:5432 postgres:16
GEXBOT_TEST_DATABASE_URL=postgresql://gexbot:gexbot@127.0.0.1:55432/gexbot \
  .venv/bin/python -m unittest discover -s tests
docker rm -f gexbot-test-pg
```

Frontend:

```bash
cd frontend
npm ci
npm run typecheck
npm run test:runtime
npm run build
```

See [docs/testing.md](docs/testing.md) for the full verification gate.

## Known Limitations

### GEX proxy assumptions

The dashboard reports a **GEX proxy**, not measured dealer positioning. It
assumes a fixed `call-positive / put-negative` position sign, uses a local
Black-Scholes gamma from a configured flat risk-free rate and dividend yield, and
weights by open interest. It does not know actual dealer inventory, hedging flow,
or customer-vs-dealer sign. `zero_gamma_proxy`, `call_wall_proxy`, and
`put_wall_proxy` are derived from that proxy and are decision-support heuristics,
not guarantees. There are no trading signals, order placement, or brokerage
integration, and none are intended.

### Complete vs partial captures

A **complete** (`success`) capture is one where every requested/selected
expiration returned data. A **partial** capture is one where at least one
expiration succeeded and at least one failed; it remains usable and is analyzed,
but analysis carries `partial_capture` and `analysis_uses_partial_capture`
warnings that persist through latest/history/run-detail/recompute. A capture
where every expiration fails, or where the DTE window selects no expirations, is
`failed` (`capture_failed` / `no_expirations_selected`) and is not stored as a
usable empty success. Missing provider fields (IV, OI, contract multiplier, spot
observed time) are kept as `null` rather than fabricated; contracts missing
inputs required for the math are excluded with explicit reasons.

### Expected behavior outside market hours

The scheduler only captures chains and runs spot analysis during the option
session — from the session open until `option_last_trade_at` (16:15 ET for
SPY/QQQ, 16:00 ET for standard single-name tickers), on NYSE trading days per the
versioned holiday/early-close calendar. Outside that window the scheduler pauses,
`GET /api/v1/gex-proxy/latest` keeps returning the last stored run, and the
dashboard polls market status every 60s so it detects the next open. Manual
`capture`/`analyze` API calls still work at any time (use them, or mock mode, to
exercise the pipeline off-hours).

See [docs/deployment.md](docs/deployment.md) for Docker, scheduler, and Tradier
deployment details.
