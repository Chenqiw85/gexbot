from __future__ import annotations

from datetime import datetime, timedelta


class PostgresLeaseLockStore:
    def __init__(self, connection) -> None:
        self.connection = connection

    def acquire(self, lock_key: str, owner_id: str, now: datetime, ttl: timedelta) -> bool:
        lease_expires_at = now + ttl
        with self.connection.transaction():
            row = self.connection.execute(
                """
                SELECT owner_id, lease_expires_at
                FROM scheduler_locks
                WHERE lock_key = %s
                FOR UPDATE
                """,
                (lock_key,),
            ).fetchone()
            if row and row[0] != owner_id and row[1] > now:
                return False
            self.connection.execute(
                """
                INSERT INTO scheduler_locks(lock_key, owner_id, lease_expires_at, last_heartbeat_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (lock_key)
                DO UPDATE SET
                  owner_id = EXCLUDED.owner_id,
                  lease_expires_at = EXCLUDED.lease_expires_at,
                  last_heartbeat_at = EXCLUDED.last_heartbeat_at
                """,
                (lock_key, owner_id, lease_expires_at, now),
            )
            return True
