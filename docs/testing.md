# Testing Gates

Run these checks after each implementation phase.

## Backend Unit And API Integration

```bash
cd backend
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall -q app tests
```

The unittest suite includes:

- GEX proxy math and expiry behavior.
- Calendar and SPY/QQQ 16:00 vs 16:15 policies.
- Partial option-chain capture.
- Tradier provider parsing.
- HTTP cache, retry, and rate-limit behavior.
- FastAPI integration workflows for custom tickers, capture, analyze, checkpoint persistence, history, run detail, and recompute.
- Historical recompute rejects unsupported model versions with a structured API error instead of silently using the current formula.
- Persistent ticker registry behavior shared by API and scheduler, including custom ticker enable/disable handling.
- Scheduler lock, dynamic ticker iteration, session gating, and exception logging.
- Provider smoke summary logic for live data-source checks.
- Startup configuration validation for provider selection, seeded tickers, refresh cadence, and DTE bounds.

## Frontend

```bash
cd frontend
npm run typecheck
npm run test:runtime
npm run build
```

`npm run typecheck` also compiles lightweight TypeScript contract checks under `frontend/tests`. `npm run test:runtime` executes small frontend runtime assertions, including dashboard form conversions that must match API units, API error formatting for structured provider and FastAPI validation errors, and replay summary labels for history key levels plus IV/OI timestamp coverage.

## Docker

```bash
docker compose config
docker compose up --build -d
docker compose ps
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:3000
docker compose logs --tail=100 api
docker compose logs --tail=100 scheduler
docker compose down
```

Use mock provider for Docker smoke tests unless Tradier credentials are intentionally being tested.

## Optional Live Tradier Smoke

Run this only when a real token is available:

```bash
cd backend
GEXBOT_PROVIDER=tradier TRADIER_TOKEN=... .venv/bin/python -m app.providers.smoke --ticker SPY --max-expirations 1
```

Acceptance: `ok=true`, positive `spot_price`, at least one expiration, non-zero `contract_count`, and `has_iv`, `has_open_interest`, and `has_contract_multiplier` are true. `spot_observed_at` may be null if Tradier does not provide a source observation timestamp. A missing contract multiplier should fail the smoke acceptance instead of being silently defaulted to 100.

The smoke command exits non-zero and prints `ok=false` when IV, open interest, or contract multiplier are completely absent from the sampled contracts.
The smoke command validates `--ticker` before provider calls; invalid symbols should fail locally without touching Tradier.
