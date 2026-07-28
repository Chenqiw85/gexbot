import unittest
from datetime import datetime, timedelta, timezone

from app.scheduler.locks import InMemoryLeaseLockStore


class SchedulerLockTest(unittest.TestCase):
    def test_lock_prevents_duplicate_work_until_lease_expires(self):
        store = InMemoryLeaseLockStore()
        now = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)

        self.assertTrue(store.acquire("chain_capture:SPY:0-45", "scheduler-a", now, timedelta(seconds=30)))
        self.assertFalse(store.acquire("chain_capture:SPY:0-45", "scheduler-b", now, timedelta(seconds=30)))
        self.assertTrue(
            store.acquire(
                "chain_capture:SPY:0-45",
                "scheduler-b",
                now + timedelta(seconds=31),
                timedelta(seconds=30),
            )
        )

    def test_same_owner_can_renew_lock(self):
        store = InMemoryLeaseLockStore()
        now = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)

        self.assertTrue(store.acquire("spot_analyze:SPY:all", "scheduler-a", now, timedelta(seconds=30)))
        self.assertTrue(store.acquire("spot_analyze:SPY:all", "scheduler-a", now + timedelta(seconds=10), timedelta(seconds=30)))


if __name__ == "__main__":
    unittest.main()
