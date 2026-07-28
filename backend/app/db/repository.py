from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from app.domain.calendar import NY, policy_for_ticker
from app.domain.gex import (
    AnalysisInput,
    AnalysisResult,
    ContractSnapshot,
    OptionType,
    PositionAssumption,
    StrikeGexProxyRow,
    analyze_gex_proxy,
    included_expiration_dates,
)
from app.domain.ticker import AppTicker
from app.providers.base import SpotQuote
from app.services.capture import ChainCapture, ExpirationCapture


@dataclass(frozen=True)
class PersistedChainCapture:
    id: int
    capture: ChainCapture


@dataclass(frozen=True)
class PersistedAnalysisRun:
    id: int
    chain_capture: ChainCapture
    chain_capture_id: int
    spot: SpotQuote
    spot_observation_id: int
    input_data: AnalysisInput
    persist_strike_rows: bool
    warnings: tuple[str, ...]
    result: AnalysisResult | None = None


class PostgresRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    @classmethod
    def connect(cls, database_url: str) -> "PostgresRepository":
        return cls(psycopg.connect(database_url, autocommit=True))

    def ensure_schema(self) -> None:
        schema = Path(__file__).with_name("schema.sql").read_text()
        self.connection.execute(schema)
        self.connection.commit()

    def ensure_underlying(self, ticker: str, dividend_yield: float) -> None:
        normalized = ticker.upper()
        self.connection.execute(
            """
            INSERT INTO underlyings(
              ticker, asset_type, enabled, dividend_yield, contract_multiplier_default,
              option_close_policy
            )
            VALUES (%s, %s, true, %s, 100, %s)
            ON CONFLICT (ticker) DO NOTHING
            """,
            (
                normalized,
                _asset_type_for_ticker(normalized),
                dividend_yield,
                policy_for_ticker(normalized).name,
            ),
        )
        self.connection.commit()

    def upsert_underlying(self, ticker: str, dividend_yield: float) -> AppTicker:
        normalized = ticker.upper()
        with self.connection.transaction():
            row = self.connection.execute(
                """
                INSERT INTO underlyings(
                  ticker, asset_type, enabled, dividend_yield, contract_multiplier_default,
                  option_close_policy
                )
                VALUES (%s, %s, true, %s, 100, %s)
                ON CONFLICT (ticker) DO UPDATE
                SET dividend_yield = EXCLUDED.dividend_yield,
                    enabled = true,
                    updated_at = now()
                RETURNING ticker, dividend_yield, enabled
                """,
                (
                    normalized,
                    _asset_type_for_ticker(normalized),
                    dividend_yield,
                    policy_for_ticker(normalized).name,
                ),
            ).fetchone()
        return _app_ticker_from_row(row)

    def update_underlying(
        self,
        ticker: str,
        *,
        enabled: bool | None,
        dividend_yield: float | None,
        default_dividend_yield: float,
    ) -> AppTicker:
        normalized = ticker.upper()
        insert_enabled = True if enabled is None else enabled
        insert_dividend_yield = default_dividend_yield if dividend_yield is None else dividend_yield
        with self.connection.transaction():
            row = self.connection.execute(
                """
                INSERT INTO underlyings(
                  ticker, asset_type, enabled, dividend_yield, contract_multiplier_default,
                  option_close_policy
                )
                VALUES (%s, %s, %s, %s, 100, %s)
                ON CONFLICT (ticker) DO UPDATE
                SET enabled = COALESCE(%s, underlyings.enabled),
                    dividend_yield = COALESCE(%s, underlyings.dividend_yield),
                    updated_at = now()
                RETURNING ticker, dividend_yield, enabled
                """,
                (
                    normalized,
                    _asset_type_for_ticker(normalized),
                    insert_enabled,
                    insert_dividend_yield,
                    policy_for_ticker(normalized).name,
                    enabled,
                    dividend_yield,
                ),
            ).fetchone()
        return _app_ticker_from_row(row)

    def list_underlyings(self) -> list[AppTicker]:
        rows = self.connection.execute(
            """
            SELECT ticker, dividend_yield, enabled
            FROM underlyings
            WHERE enabled = true
            ORDER BY ticker
            """
        ).fetchall()
        return [_app_ticker_from_row(row) for row in rows]

    def save_chain_capture(self, capture: ChainCapture) -> int:
        with self.connection.transaction():
            chain_id = self.connection.execute(
                """
                INSERT INTO chain_captures(
                  ticker, source, status, min_dte, max_dte, requested_expirations,
                  chain_captured_at, provider_received_at, warnings
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    capture.ticker,
                    capture.source,
                    capture.status,
                    capture.min_dte,
                    capture.max_dte,
                    capture.requested_expirations,
                    capture.chain_captured_at,
                    capture.provider_received_at,
                    list(capture.warnings),
                ),
            ).fetchone()[0]
            for expiration in capture.expiration_captures:
                expiration_id = self.connection.execute(
                    """
                    INSERT INTO expiration_captures(
                      chain_capture_id, expiration_date, underlying_close_at,
                      option_last_trade_at, status, provider_observed_at, error_message
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        chain_id,
                        expiration.expiration_date,
                        expiration.underlying_close_at,
                        expiration.option_last_trade_at,
                        expiration.status,
                        expiration.provider_observed_at,
                        expiration.error_message,
                    ),
                ).fetchone()[0]
                for contract in expiration.contracts:
                    self.connection.execute(
                        """
                        INSERT INTO option_contract_captures(
                          expiration_capture_id, option_symbol, strike, option_type,
                          contract_multiplier, open_interest, oi_observed_date, volume,
                          implied_volatility, iv_observed_at, vendor_delta, vendor_gamma,
                          vendor_theta, vendor_vega, bid, ask, last, mark
                        )
                        VALUES (
                          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            expiration_id,
                            contract.option_symbol,
                            contract.strike,
                            contract.option_type.value,
                            contract.contract_multiplier,
                            contract.open_interest,
                            contract.oi_observed_date,
                            contract.volume,
                            contract.implied_volatility,
                            contract.iv_observed_at,
                            contract.vendor_delta,
                            contract.vendor_gamma,
                            contract.vendor_theta,
                            contract.vendor_vega,
                            contract.bid,
                            contract.ask,
                            contract.last,
                            contract.mark,
                        ),
                    )
            return int(chain_id)

    def latest_chain_capture(self, ticker: str) -> PersistedChainCapture | None:
        row = self.connection.execute(
            """
            SELECT id
            FROM chain_captures
            WHERE ticker = %s AND status IN ('success', 'partial')
            ORDER BY chain_captured_at DESC, id DESC
            LIMIT 1
            """,
            (ticker.upper(),),
        ).fetchone()
        if row is None:
            return None
        return self.load_chain_capture(int(row[0]))

    def load_chain_capture(self, chain_capture_id: int) -> PersistedChainCapture:
        chain = self.connection.execute(
            """
            SELECT id, ticker, source, status, min_dte, max_dte, requested_expirations,
                   chain_captured_at, provider_received_at, warnings
            FROM chain_captures
            WHERE id = %s
            """,
            (chain_capture_id,),
        ).fetchone()
        if chain is None:
            raise KeyError(f"chain capture {chain_capture_id} not found")

        expiration_rows = self.connection.execute(
            """
            SELECT id, expiration_date, underlying_close_at, option_last_trade_at,
                   status, provider_observed_at, error_message
            FROM expiration_captures
            WHERE chain_capture_id = %s
            ORDER BY expiration_date
            """,
            (chain_capture_id,),
        ).fetchall()

        expirations: list[ExpirationCapture] = []
        for expiration in expiration_rows:
            contracts = self.connection.execute(
                """
                SELECT option_symbol, strike, option_type, open_interest, volume,
                       implied_volatility, iv_observed_at, oi_observed_date,
                       contract_multiplier, bid, ask, last, mark,
                       vendor_delta, vendor_gamma, vendor_theta, vendor_vega
                FROM option_contract_captures
                WHERE expiration_capture_id = %s
                ORDER BY strike, option_type
                """,
                (expiration[0],),
            ).fetchall()
            expirations.append(
                ExpirationCapture(
                    expiration_date=expiration[1],
                    underlying_close_at=expiration[2],
                    option_last_trade_at=expiration[3],
                    status=expiration[4],
                    provider_observed_at=expiration[5],
                    error_message=expiration[6],
                    contracts=[
                        ContractSnapshot(
                            option_symbol=item[0],
                            strike=_float(item[1]),
                            option_type=OptionType(item[2]),
                            open_interest=item[3],
                            volume=item[4],
                            implied_volatility=_optional_float(item[5]),
                            iv_observed_at=item[6],
                            oi_observed_date=item[7],
                            contract_multiplier=item[8],
                            underlying_close_at=expiration[2],
                            option_last_trade_at=expiration[3],
                            bid=_optional_float(item[9]),
                            ask=_optional_float(item[10]),
                            last=_optional_float(item[11]),
                            mark=_optional_float(item[12]),
                            vendor_delta=_optional_float(item[13]),
                            vendor_gamma=_optional_float(item[14]),
                            vendor_theta=_optional_float(item[15]),
                            vendor_vega=_optional_float(item[16]),
                        )
                        for item in contracts
                    ],
                )
            )

        capture = ChainCapture(
            ticker=chain[1],
            source=chain[2],
            status=chain[3],
            min_dte=chain[4],
            max_dte=chain[5],
            requested_expirations=list(chain[6]),
            chain_captured_at=chain[7],
            provider_received_at=chain[8],
            expiration_captures=expirations,
            warnings=tuple(chain[9] or ()),
        )
        return PersistedChainCapture(id=int(chain[0]), capture=capture)

    def save_spot_observation(self, spot: SpotQuote, source: str) -> int:
        with self.connection.transaction():
            return int(
                self.connection.execute(
                    """
                    INSERT INTO spot_observations(
                      ticker, spot_price, source, spot_observed_at, provider_received_at
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        spot.ticker,
                        spot.spot_price,
                        source,
                        spot.spot_observed_at,
                        spot.provider_received_at,
                    ),
                ).fetchone()[0]
            )

    def save_analysis_run(
        self,
        *,
        chain_capture_id: int,
        spot_observation_id: int,
        input_data: AnalysisInput,
        result,
        persist_strike_rows: bool,
        warnings: tuple[str, ...],
        min_dte: int,
        max_dte: int,
        idempotency_key: str | None = None,
    ) -> int:
        included_expirations = list(included_expiration_dates(input_data))
        insert_sql = """
                INSERT INTO analysis_runs(
                  chain_capture_id, spot_observation_id, ticker, scope, analyzed_at,
                  min_dte, max_dte, included_expirations, pricing_model, model_version,
                  calendar_version, option_close_policy_version, position_assumption,
                  risk_free_rate, risk_free_rate_source, dividend_yield, dividend_yield_source,
                  excluded_contract_count, net_gex_proxy, zero_gamma_proxy, call_wall_proxy,
                  put_wall_proxy, persist_strike_rows, warnings, idempotency_key
                )
                VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """
        params = (
            chain_capture_id,
            spot_observation_id,
            input_data.ticker,
            input_data.scope,
            input_data.analyzed_at,
            min_dte,
            max_dte,
            included_expirations,
            result.model_inputs.pricing_model,
            result.model_inputs.model_version,
            result.model_inputs.calendar_version,
            result.model_inputs.option_close_policy_version,
            result.model_inputs.position_assumption.value,
            result.model_inputs.risk_free_rate,
            result.model_inputs.risk_free_rate_source,
            result.model_inputs.dividend_yield,
            result.model_inputs.dividend_yield_source,
            result.excluded_contract_count,
            result.net_gex_proxy,
            result.zero_gamma_proxy,
            result.call_wall_proxy,
            result.put_wall_proxy,
            persist_strike_rows,
            list(warnings),
            idempotency_key,
        )
        with self.connection.transaction():
            if idempotency_key is None:
                row = self.connection.execute(
                    f"""
                    {insert_sql}
                    RETURNING id, true AS inserted
                    """,
                    params,
                ).fetchone()
            else:
                row = self.connection.execute(
                    f"""
                    WITH inserted AS (
                    {insert_sql}
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING id
                    )
                    SELECT id, true AS inserted FROM inserted
                    UNION ALL
                    SELECT id, false AS inserted
                    FROM analysis_runs
                    WHERE idempotency_key = %s
                    LIMIT 1
                    """,
                    params + (idempotency_key,),
                ).fetchone()
            run_id = int(row[0])
            was_inserted = bool(row[1]) if len(row) > 1 else True
            if persist_strike_rows and was_inserted:
                self._insert_strike_rows(run_id, result.rows.values())
            return int(run_id)

    def persist_analysis_run_rows(self, run_id: int) -> PersistedAnalysisRun:
        persisted = self.load_analysis_run(run_id)
        result = analyze_gex_proxy(persisted.input_data)
        with self.connection.transaction():
            self.connection.execute("DELETE FROM strike_gex_proxy WHERE analysis_run_id = %s", (run_id,))
            self._insert_strike_rows(run_id, result.rows.values())
            self.connection.execute(
                "UPDATE analysis_runs SET persist_strike_rows = true WHERE id = %s",
                (run_id,),
            )
        return self.load_analysis_run(run_id)

    def latest_analysis_run(self, ticker: str, scope: str) -> PersistedAnalysisRun | None:
        row = self.connection.execute(
            """
            SELECT id
            FROM analysis_runs
            WHERE ticker = %s AND scope = %s
            ORDER BY analyzed_at DESC, id DESC
            LIMIT 1
            """,
            (ticker.upper(), scope),
        ).fetchone()
        if row is None:
            return None
        return self.load_analysis_run(int(row[0]))

    def analysis_run_by_idempotency_key(self, idempotency_key: str) -> PersistedAnalysisRun | None:
        row = self.connection.execute(
            """
            SELECT id
            FROM analysis_runs
            WHERE idempotency_key = %s
            LIMIT 1
            """,
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return self.load_analysis_run(int(row[0]))

    def history_runs(
        self,
        ticker: str,
        scope: str,
        date_filter: date | None = None,
        limit: int = 2000,
    ) -> list[PersistedAnalysisRun]:
        if date_filter is None:
            rows = self.connection.execute(
                """
                SELECT id
                FROM analysis_runs
                WHERE ticker = %s AND scope = %s
                ORDER BY analyzed_at ASC, id ASC
                LIMIT %s
                """,
                (ticker.upper(), scope, limit),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT id
                FROM analysis_runs
                WHERE ticker = %s
                  AND scope = %s
                  AND (analyzed_at AT TIME ZONE 'America/New_York')::date = %s
                ORDER BY analyzed_at ASC, id ASC
                LIMIT %s
                """,
                (ticker.upper(), scope, date_filter, limit),
            ).fetchall()
        return [self.load_analysis_run(int(row[0])) for row in rows]

    def load_analysis_run(self, run_id: int) -> PersistedAnalysisRun:
        row = self.connection.execute(
            """
            SELECT id, chain_capture_id, spot_observation_id, ticker, scope, analyzed_at,
                   pricing_model, model_version, calendar_version, option_close_policy_version,
                   position_assumption, risk_free_rate, risk_free_rate_source,
                   dividend_yield, dividend_yield_source, persist_strike_rows, warnings,
                   excluded_contract_count, net_gex_proxy, zero_gamma_proxy,
                   call_wall_proxy, put_wall_proxy
            FROM analysis_runs
            WHERE id = %s
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"analysis run {run_id} not found")
        spot_row = self.connection.execute(
            """
            SELECT ticker, spot_price, source, spot_observed_at, provider_received_at
            FROM spot_observations
            WHERE id = %s
            """,
            (row[2],),
        ).fetchone()
        capture = self.load_chain_capture(int(row[1]))
        spot = SpotQuote(
            ticker=spot_row[0],
            spot_price=_float(spot_row[1]),
            spot_observed_at=spot_row[3],
            provider_received_at=spot_row[4],
        )
        contracts = [
            contract
            for expiration in capture.capture.expiration_captures
            if expiration.status == "success"
            for contract in expiration.contracts
            if row[4] != "0dte"
            or contract.underlying_close_at.astimezone(NY).date() == row[5].astimezone(NY).date()
        ]
        input_data = AnalysisInput(
            ticker=row[3],
            scope=row[4],
            spot_price=spot.spot_price,
            spot_observed_at=spot.spot_observed_at,
            spot_provider_received_at=spot.provider_received_at,
            analyzed_at=row[5],
            risk_free_rate=_float(row[11]),
            dividend_yield=_float(row[13]),
            position_assumption=PositionAssumption(row[10]),
            contracts=contracts,
            pricing_model=row[6],
            model_version=row[7],
            calendar_version=row[8],
            option_close_policy_version=row[9],
            risk_free_rate_source=row[12],
            dividend_yield_source=row[14],
        )
        recalculated = analyze_gex_proxy(input_data)
        rows = self._load_strike_rows(int(row[0])) if row[15] else recalculated.rows
        result = AnalysisResult(
            rows=rows,
            net_gex_proxy=_float(row[18]),
            zero_gamma_proxy=_optional_float(row[19]),
            call_wall_proxy=_optional_float(row[20]),
            put_wall_proxy=_optional_float(row[21]),
            excluded_contract_count=int(row[17]),
            warnings=tuple(row[16] or ()),
            model_inputs=recalculated.model_inputs,
        )
        return PersistedAnalysisRun(
            id=int(row[0]),
            chain_capture=capture.capture,
            chain_capture_id=int(row[1]),
            spot=spot,
            spot_observation_id=int(row[2]),
            input_data=input_data,
            persist_strike_rows=row[15],
            warnings=tuple(row[16] or ()),
            result=result,
        )

    def _load_strike_rows(self, run_id: int) -> dict[float, StrikeGexProxyRow]:
        rows = self.connection.execute(
            """
            SELECT strike, call_gex_proxy, put_gex_proxy, net_gex_proxy,
                   call_oi, put_oi, call_volume, put_volume
            FROM strike_gex_proxy
            WHERE analysis_run_id = %s
            ORDER BY strike
            """,
            (run_id,),
        ).fetchall()
        return {
            _float(row[0]): StrikeGexProxyRow(
                strike=_float(row[0]),
                call_gex_proxy=_float(row[1]),
                put_gex_proxy=_float(row[2]),
                net_gex_proxy=_float(row[3]),
                call_oi=int(row[4]),
                put_oi=int(row[5]),
                call_volume=int(row[6]),
                put_volume=int(row[7]),
            )
            for row in rows
        }

    def _insert_strike_rows(self, run_id: int, rows) -> None:
        for row in rows:
            self.connection.execute(
                """
                INSERT INTO strike_gex_proxy(
                  analysis_run_id, strike, call_gex_proxy, put_gex_proxy,
                  net_gex_proxy, call_oi, put_oi, call_volume, put_volume
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    row.strike,
                    row.call_gex_proxy,
                    row.put_gex_proxy,
                    row.net_gex_proxy,
                    row.call_oi,
                    row.put_oi,
                    row.call_volume,
                    row.put_volume,
                ),
            )


def _float(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return _float(value)


def _app_ticker_from_row(row) -> AppTicker:
    return AppTicker(ticker=row[0], dividend_yield=_float(row[1]), enabled=bool(row[2]))


def _asset_type_for_ticker(ticker: str) -> str:
    return "etf" if ticker.upper() in {"SPY", "QQQ"} else "equity"
