from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Lease:
    lock_key: str
    owner_id: str
    lease_expires_at: datetime
    last_heartbeat_at: datetime


class InMemoryLeaseLockStore:
    def __init__(self) -> None:
        self._leases: dict[str, Lease] = {}

    def acquire(self, lock_key: str, owner_id: str, now: datetime, ttl: timedelta) -> bool:
        existing = self._leases.get(lock_key)
        if existing and existing.owner_id != owner_id and existing.lease_expires_at > now:
            return False
        self._leases[lock_key] = Lease(
            lock_key=lock_key,
            owner_id=owner_id,
            lease_expires_at=now + ttl,
            last_heartbeat_at=now,
        )
        return True
