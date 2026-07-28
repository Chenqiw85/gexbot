from __future__ import annotations

from psycopg_pool import ConnectionPool


def build_pool(
    database_url: str,
    *,
    min_size: int = 1,
    max_size: int = 5,
    timeout: float = 10.0,
) -> ConnectionPool:
    """Build an autocommit connection pool for the repository.

    Each checked-out connection is autocommit so that single statements commit
    immediately, while multi-statement units of work use an explicit
    ``connection.transaction()`` block to preserve atomicity. This mirrors the
    previous single-connection behaviour without sharing one connection across
    request threads.
    """
    return ConnectionPool(
        conninfo=database_url,
        min_size=min_size,
        max_size=max_size,
        timeout=timeout,
        kwargs={"autocommit": True},
        open=True,
    )
