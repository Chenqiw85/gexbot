from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
import time
from uuid import uuid4

from app.config import get_settings
from app.db.locks import PostgresLeaseLockStore
from app.domain.calendar import NY, build_expiration_times, policy_for_ticker, MarketCalendar
from app.scheduler.locks import InMemoryLeaseLockStore
from app.services.capture import ChainCaptureRequest
from app.services.orchestrator import RuntimeSpotObservation, RuntimeState


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("gexbot.scheduler")
ANALYSIS_SCOPES = ("all", "0dte")


def run_forever() -> None:
    settings = get_settings()
    owner_id = os.getenv("SCHEDULER_OWNER_ID", f"scheduler-{uuid4()}")
    state = RuntimeState(settings=settings)
    locks = _make_lock_store(state)
    last_chain: dict[str, float] = {}
    last_spot: dict[str, float] = {}

    logger.info("scheduler started owner_id=%s provider=%s", owner_id, settings.provider)
    while True:
        now = datetime.now(timezone.utc)
        _run_due_jobs_once(state, locks, owner_id, now, last_chain, last_spot)
        time.sleep(1)


def _run_due_jobs_once(
    state: RuntimeState,
    locks,
    owner_id: str,
    now: datetime,
    last_chain: dict[str, float],
    last_spot: dict[str, float],
) -> None:
    for ticker in state.ticker_symbols():
        _run_chain_job_if_due(state, locks, owner_id, ticker, now, last_chain)
        spot_observations: dict[str, RuntimeSpotObservation] = {}
        for scope in ANALYSIS_SCOPES:
            _run_spot_job_if_due(
                state,
                locks,
                owner_id,
                ticker,
                now,
                last_spot,
                scope=scope,
                spot_observations=spot_observations,
            )


def _run_chain_job_if_due(
    state: RuntimeState,
    locks,
    owner_id: str,
    ticker: str,
    now: datetime,
    last_chain: dict[str, float],
) -> None:
    settings = state.settings
    if not _is_option_session_open(ticker, now):
        return
    if now.timestamp() - last_chain.get(ticker, 0) < settings.chain_refresh_seconds:
        return
    lock_key = _chain_capture_lock_key(ticker, now, settings.chain_refresh_seconds, settings)
    if not locks.acquire(lock_key, owner_id, now, timedelta(seconds=settings.chain_refresh_seconds)):
        return
    try:
        capture = state.capture_chain(
            ChainCaptureRequest(
                ticker=ticker,
                min_dte=settings.default_min_dte,
                max_dte=settings.default_max_dte,
                max_expirations=settings.default_max_expirations,
            )
        )
    except Exception:
        logger.exception("chain capture failed ticker=%s", ticker)
        return
    else:
        logger.info(
            "chain capture ticker=%s status=%s expirations=%s",
            ticker,
            capture.status,
            len(capture.expiration_captures),
        )
    finally:
        last_chain[ticker] = now.timestamp()


def _run_spot_job_if_due(
    state: RuntimeState,
    locks,
    owner_id: str,
    ticker: str,
    now: datetime,
    last_spot: dict[str, float],
    *,
    scope: str = "all",
    spot_observations: dict[str, RuntimeSpotObservation] | None = None,
) -> None:
    settings = state.settings
    if not _is_option_session_open(ticker, now):
        return
    throttle_key = f"{ticker}:{scope}"
    if now.timestamp() - last_spot.get(throttle_key, 0) < settings.spot_refresh_seconds:
        return
    lock_key = f"spot_analyze:{ticker}:{scope}"
    if not locks.acquire(lock_key, owner_id, now, timedelta(seconds=settings.spot_refresh_seconds)):
        return
    try:
        def spot_observation_for_ticker() -> RuntimeSpotObservation:
            if spot_observations is None:
                return state.observe_spot(ticker)
            normalized = ticker.upper()
            existing = spot_observations.get(normalized)
            if existing is not None:
                return existing
            observed = state.observe_spot(normalized)
            spot_observations[normalized] = observed
            return observed

        run = state.analyze_latest(
            ticker=ticker,
            scope=scope,
            persist_strike_rows=False,
            allow_chain_capture=False,
            idempotency_key=_spot_analysis_idempotency_key(ticker, scope, now, settings.spot_refresh_seconds),
            spot_observation_factory=spot_observation_for_ticker,
        )
    except Exception:
        logger.exception("spot analyze failed ticker=%s scope=%s", ticker, scope)
        return
    else:
        logger.info("spot analyze ticker=%s scope=%s run_id=%s net=%s", ticker, scope, run.id, run.result.net_gex_proxy)
    finally:
        last_spot[throttle_key] = now.timestamp()


def _make_lock_store(state: RuntimeState):
    if state.repository:
        return PostgresLeaseLockStore(state.repository.connection)
    return InMemoryLeaseLockStore()


def _spot_analysis_idempotency_key(ticker: str, scope: str, now: datetime, interval_seconds: int) -> str:
    bucket_start = _bucket_start(now, interval_seconds)
    return f"spot_analyze:{ticker.upper()}:{scope}:{bucket_start.isoformat()}"


def _chain_capture_lock_key(ticker: str, now: datetime, interval_seconds: int, settings) -> str:
    bucket_start = _bucket_start(now, interval_seconds)
    return (
        f"chain_capture:{ticker.upper()}:{settings.default_min_dte}-{settings.default_max_dte}:"
        f"max{settings.default_max_expirations}:{bucket_start.isoformat()}"
    )


def _bucket_start(now: datetime, interval_seconds: int) -> datetime:
    interval = max(1, interval_seconds)
    bucket_timestamp = int(now.timestamp()) - (int(now.timestamp()) % interval)
    return datetime.fromtimestamp(bucket_timestamp, tz=timezone.utc)


def _is_option_session_open(ticker: str, now: datetime) -> bool:
    calendar = MarketCalendar()
    local_now = now.astimezone(NY)
    if not calendar.is_trading_day(local_now.date()):
        return False
    session = calendar.session_for(local_now.date())
    expiration_times = build_expiration_times(
        ticker,
        local_now.date(),
        policy_for_ticker(ticker),
        market_calendar=calendar,
    )
    return session.open_at <= local_now <= expiration_times.option_last_trade_at


if __name__ == "__main__":
    run_forever()
