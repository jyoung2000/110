"""Rate limiter for concurrent downloads and API calls."""
import asyncio
import random
import time


class RateLimiter:
    """Simple semaphore-based rate limiter with per-domain cooldown and jitter."""

    def __init__(self, max_concurrent: int = 3, min_delay: float = 1.0):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.min_delay = min_delay
        self._domain_last_access: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, domain: str = ""):
        """Acquire a slot, respecting per-domain delay with jitter."""
        await self.semaphore.acquire()
        if domain and self.min_delay > 0:
            async with self._lock:
                last = self._domain_last_access.get(domain, 0)
                now = time.time()
                # Add 0-50% jitter to the minimum delay
                jittered_delay = self.min_delay * (1.0 + random.random() * 0.5)
                wait = jittered_delay - (now - last)
                if wait > 0:
                    await asyncio.sleep(wait)
                self._domain_last_access[domain] = time.time()

    def release(self):
        """Release a slot."""
        self.semaphore.release()

    async def __aenter__(self):
        await self.semaphore.acquire()
        return self

    async def __aexit__(self, *args):
        self.semaphore.release()
