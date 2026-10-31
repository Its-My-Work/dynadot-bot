"""Serialising priority queue for outbound Dynadot requests.

Dynadot's dominant constraint: *"Only 1 request can be processed at a time."*
Every call therefore goes through this queue, which enforces both the
concurrency cap and the per-minute rate limit, and lets revenue-critical work
(registration) jump ahead of user-facing search.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, TypeVar

from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class Priority(IntEnum):
    """Lower value drains first."""

    CRITICAL = 0  # register, renew — money already taken
    HIGH = 10  # nameserver / DNS writes
    NORMAL = 50  # account info, domain list
    LOW = 100  # search, price sync


@dataclass(order=True)
class _QueueItem:
    priority: int
    sequence: int
    future: asyncio.Future = field(compare=False)
    operation: Callable[[], Awaitable[Any]] = field(compare=False)
    name: str = field(compare=False, default="")


class RateLimiter:
    """Sliding-window limiter capping calls per 60 seconds."""

    def __init__(self, max_per_minute: int) -> None:
        self._max = max(1, max_per_minute)
        self._timestamps: list[float] = []

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            cutoff = now - 60.0
            self._timestamps = [t for t in self._timestamps if t > cutoff]

            if len(self._timestamps) < self._max:
                self._timestamps.append(now)
                return

            sleep_for = 60.0 - (now - self._timestamps[0]) + 0.01
            logger.debug("rate limit reached, waiting", extra={"sleep_s": sleep_for})
            await asyncio.sleep(max(sleep_for, 0.01))


class DynadotQueue:
    """Single-worker priority queue in front of the Dynadot API."""

    def __init__(self, *, max_per_minute: int = 60, concurrency: int = 1) -> None:
        self._queue: asyncio.PriorityQueue[_QueueItem] = asyncio.PriorityQueue()
        self._limiter = RateLimiter(max_per_minute)
        self._concurrency = max(1, concurrency)
        self._counter = itertools.count()
        self._workers: list[asyncio.Task[None]] = []
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._workers = [
            asyncio.create_task(self._worker(i), name=f"dynadot-worker-{i}")
            for i in range(self._concurrency)
        ]
        logger.info("dynadot queue started", extra={"workers": self._concurrency})

    async def stop(self) -> None:
        """Cancel workers and fail anything still pending."""
        self._running = False
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            try:
                await task
            except asyncio.CancelledError:
                pass  # expected: we cancelled it above
            except Exception:
                logger.exception("dynadot worker raised during shutdown")
        self._workers.clear()

        while not self._queue.empty():
            item = self._queue.get_nowait()
            if not item.future.done():
                item.future.set_exception(RuntimeError("Dynadot queue stopped"))
        logger.info("dynadot queue stopped")

    async def submit(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        priority: Priority | int = Priority.NORMAL,
        name: str = "",
    ) -> T:
        """Enqueue a coroutine factory and await its result."""
        if not self._running:
            # Degrade to a direct call so scripts/tests work without a worker.
            await self._limiter.acquire()
            return await operation()

        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        self._queue.put_nowait(
            _QueueItem(
                priority=int(priority),
                sequence=next(self._counter),
                future=future,
                operation=operation,
                name=name,
            )
        )
        try:
            return await future
        except asyncio.CancelledError:
            future.cancel()
            raise

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    async def _worker(self, index: int) -> None:
        while self._running:
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                return

            try:
                if item.future.cancelled():
                    continue
                await self._limiter.acquire()
                if item.future.cancelled():
                    continue
                result = await item.operation()
                if not item.future.done():
                    item.future.set_result(result)
            except asyncio.CancelledError:
                if not item.future.done():
                    item.future.set_exception(RuntimeError("worker cancelled"))
                raise
            except Exception as exc:
                if not item.future.done():
                    item.future.set_exception(exc)
            finally:
                self._queue.task_done()
