from __future__ import annotations

from datetime import datetime, timedelta


class PostgresLeaseLockStore:
    """Lease-based lock store backed by the repository connection pool.

    A connection is checked out for the duration of a single acquire attempt and
    the ``SELECT ... FOR UPDATE`` plus upsert run in one transaction on that one
    connection, so the lease decision is atomic and no connection is shared
    across cycles or threads.
    """

    def __init__(self, repository) -> None:
        self.repository = repository

    def acquire(self, lock_key: str, owner_id: str, now: datetime, ttl: timedelta) -> bool:
        lease_expires_at = now + ttl
        # A single atomic INSERT ... ON CONFLICT DO UPDATE decides ownership. The
        # conditional UPDATE only fires when the existing lease has expired
        # (``lease_expires_at <= now``) or we already own it (renewal). A brand-new
        # lock key is created by the INSERT branch. Because the conflicting row is
        # locked for the duration of the upsert, two concurrent acquirers for the
        # same key cannot both win — unlike a SELECT ... FOR UPDATE that cannot
        # lock a not-yet-existing row. We acquired the lease iff the statement
        # returns a row whose owner is us.
        with self.repository.checkout() as connection:
            row = connection.execute(
                """
                INSERT INTO scheduler_locks(lock_key, owner_id, lease_expires_at, last_heartbeat_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (lock_key)
                DO UPDATE SET
                  owner_id = EXCLUDED.owner_id,
                  lease_expires_at = EXCLUDED.lease_expires_at,
                  last_heartbeat_at = EXCLUDED.last_heartbeat_at
                WHERE scheduler_locks.lease_expires_at <= EXCLUDED.last_heartbeat_at
                   OR scheduler_locks.owner_id = EXCLUDED.owner_id
                RETURNING owner_id
                """,
                (lock_key, owner_id, lease_expires_at, now),
            ).fetchone()
        return row is not None and row[0] == owner_id
