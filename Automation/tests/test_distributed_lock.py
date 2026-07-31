import unittest

from core.distributed_lock import InMemoryDistributedLock, LockLease, RedisDistributedLock


class FakeClock:
    value = 0.0
    def __call__(self): return self.value


class FakeRedisLock:
    def __init__(self, clock): self.clock = clock; self.values = {}; self.fail = False
    def _active(self, key):
        value = self.values.get(key)
        if value and value[1] <= self.clock.value: self.values.pop(key, None); value = None
        return value
    def set(self, key, token, nx, px):
        if self.fail: raise OSError("redis://private")
        if nx and self._active(key): return False
        self.values[key] = (token, self.clock.value + px / 1000); return True
    def eval(self, script, count, key, token, *args):
        if self.fail: raise OSError("redis://private")
        current = self._active(key)
        if not current or current[0] != token: return 0
        if "del" in script: self.values.pop(key, None); return 1
        self.values[key] = (token, self.clock.value + int(args[0]) / 1000); return 1


class DistributedLockTests(unittest.TestCase):
    def test_memory_acquire_release_and_wrong_owner(self):
        lock = InMemoryDistributedLock()
        lease = lock.acquire("ws", "job", 10)
        self.assertIsNotNone(lease)
        self.assertIsNone(lock.acquire("ws", "job", 10))
        wrong = LockLease("ws", "job", "wrong", 10)
        self.assertFalse(lock.release(wrong))
        self.assertTrue(lock.release(lease))
        self.assertIsNotNone(lock.acquire("ws", "job", 10))

    def test_memory_ttl_expiry_and_workspace_isolation(self):
        clock = FakeClock(); lock = InMemoryDistributedLock(clock)
        self.assertIsNotNone(lock.acquire("a", "same", 2))
        self.assertIsNotNone(lock.acquire("b", "same", 2))
        clock.value = 3
        self.assertIsNotNone(lock.acquire("a", "same", 2))

    def test_redis_atomic_ownership_ttl_and_renewal(self):
        clock = FakeClock(); redis = FakeRedisLock(clock); lock = RedisDistributedLock(redis, "test")
        lease = lock.acquire("ws", "job", 2, "owner-a")
        self.assertIsNone(lock.acquire("ws", "job", 2, "owner-b"))
        self.assertFalse(lock.release(LockLease("ws", "job", "owner-b", 2)))
        clock.value = 1
        self.assertTrue(lock.renew(lease))
        clock.value = 2.5
        self.assertIsNone(lock.acquire("ws", "job", 2, "owner-b"))
        clock.value = 3.1
        self.assertIsNotNone(lock.acquire("ws", "job", 2, "owner-b"))

    def test_redis_failure_is_safe(self):
        clock = FakeClock(); redis = FakeRedisLock(clock); redis.fail = True
        with self.assertRaisesRegex(RuntimeError, "distributed_lock_unavailable") as raised:
            RedisDistributedLock(redis).acquire("ws", "job", 1)
        self.assertNotIn("private", str(raised.exception))


if __name__ == "__main__": unittest.main()
