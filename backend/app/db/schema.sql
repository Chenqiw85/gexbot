CREATE TABLE IF NOT EXISTS underlyings (
  id BIGSERIAL PRIMARY KEY,
  ticker TEXT NOT NULL UNIQUE,
  asset_type TEXT NOT NULL DEFAULT 'etf',
  enabled BOOLEAN NOT NULL DEFAULT true,
  dividend_yield NUMERIC NOT NULL DEFAULT 0,
  contract_multiplier_default INTEGER NOT NULL DEFAULT 100,
  option_close_policy TEXT NOT NULL DEFAULT 'standard_1600',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chain_captures (
  id BIGSERIAL PRIMARY KEY,
  ticker TEXT NOT NULL,
  source TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('success', 'partial', 'failed')),
  min_dte INTEGER NOT NULL,
  max_dte INTEGER NOT NULL,
  requested_expirations DATE[] NOT NULL,
  chain_captured_at TIMESTAMPTZ NOT NULL,
  provider_received_at TIMESTAMPTZ NOT NULL,
  raw_payload_json JSONB,
  raw_checksum TEXT,
  warnings TEXT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS expiration_captures (
  id BIGSERIAL PRIMARY KEY,
  chain_capture_id BIGINT NOT NULL REFERENCES chain_captures(id) ON DELETE CASCADE,
  expiration_date DATE NOT NULL,
  underlying_close_at TIMESTAMPTZ NOT NULL,
  option_last_trade_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
  provider_observed_at TIMESTAMPTZ,
  error_message TEXT
);

CREATE TABLE IF NOT EXISTS option_contract_captures (
  id BIGSERIAL PRIMARY KEY,
  expiration_capture_id BIGINT NOT NULL REFERENCES expiration_captures(id) ON DELETE CASCADE,
  option_symbol TEXT NOT NULL,
  strike NUMERIC NOT NULL,
  option_type TEXT NOT NULL CHECK (option_type IN ('call', 'put')),
  contract_multiplier INTEGER,
  open_interest INTEGER,
  oi_observed_date DATE,
  volume INTEGER,
  implied_volatility NUMERIC,
  iv_observed_at TIMESTAMPTZ,
  vendor_delta NUMERIC,
  vendor_gamma NUMERIC,
  vendor_theta NUMERIC,
  vendor_vega NUMERIC,
  bid NUMERIC,
  ask NUMERIC,
  last NUMERIC,
  mark NUMERIC
);

CREATE TABLE IF NOT EXISTS spot_observations (
  id BIGSERIAL PRIMARY KEY,
  ticker TEXT NOT NULL,
  spot_price NUMERIC NOT NULL,
  source TEXT NOT NULL,
  spot_observed_at TIMESTAMPTZ,
  provider_received_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_runs (
  id BIGSERIAL PRIMARY KEY,
  chain_capture_id BIGINT NOT NULL REFERENCES chain_captures(id),
  spot_observation_id BIGINT NOT NULL REFERENCES spot_observations(id),
  ticker TEXT NOT NULL,
  scope TEXT NOT NULL CHECK (scope IN ('all', '0dte')),
  analyzed_at TIMESTAMPTZ NOT NULL,
  min_dte INTEGER NOT NULL,
  max_dte INTEGER NOT NULL,
  included_expirations DATE[] NOT NULL,
  pricing_model TEXT NOT NULL,
  model_version TEXT NOT NULL,
  calendar_version TEXT NOT NULL,
  option_close_policy_version TEXT NOT NULL,
  position_assumption TEXT NOT NULL,
  risk_free_rate NUMERIC NOT NULL,
  risk_free_rate_source TEXT NOT NULL,
  dividend_yield NUMERIC NOT NULL,
  dividend_yield_source TEXT NOT NULL,
  excluded_contract_count INTEGER NOT NULL,
  net_gex_proxy NUMERIC,
  zero_gamma_proxy NUMERIC,
  call_wall_proxy NUMERIC,
  put_wall_proxy NUMERIC,
  persist_strike_rows BOOLEAN NOT NULL DEFAULT false,
  warnings TEXT[] NOT NULL DEFAULT '{}',
  idempotency_key TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS strike_gex_proxy (
  id BIGSERIAL PRIMARY KEY,
  analysis_run_id BIGINT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
  strike NUMERIC NOT NULL,
  call_gex_proxy NUMERIC NOT NULL,
  put_gex_proxy NUMERIC NOT NULL,
  net_gex_proxy NUMERIC NOT NULL,
  call_oi INTEGER NOT NULL,
  put_oi INTEGER NOT NULL,
  call_volume INTEGER NOT NULL,
  put_volume INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduler_locks (
  lock_key TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  lease_expires_at TIMESTAMPTZ NOT NULL,
  last_heartbeat_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chain_captures_latest ON chain_captures(ticker, chain_captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_expiration_captures_capture ON expiration_captures(chain_capture_id, expiration_date);
CREATE INDEX IF NOT EXISTS idx_contract_captures_expiration ON option_contract_captures(expiration_capture_id, strike, option_type);
CREATE INDEX IF NOT EXISTS idx_spot_observations_latest ON spot_observations(ticker, provider_received_at DESC);
CREATE INDEX IF NOT EXISTS idx_analysis_runs_latest ON analysis_runs(ticker, scope, analyzed_at DESC);
CREATE INDEX IF NOT EXISTS idx_strike_gex_proxy_run ON strike_gex_proxy(analysis_run_id, strike);
