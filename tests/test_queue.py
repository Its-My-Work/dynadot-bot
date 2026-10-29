"""Tests for the serialising Dynadot queue and rate limiter."""

from __future__ import annotations

import asyncio

import pytest

from app.integrations.dynadot.queue import DynadotQueue, Priority, RateLimiter


@pytest.mark.asyncio
async def test_queue_runs_one_request_at_a_time():
    """Dynadot allows a single concurrent request — verify we never exceed it."""
    queue = DynadotQueue(max_per_minute=6000, concurrency=1)
    await queue.start()

    concurrent = 0
    peak = 0

    async def op():
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.02)
        concurrent -= 1
        return "ok"

    try:
        results = await asyncio.gather(*(queue.submit(op) for _ in range(8)))
    finally:
        await queue.stop()

    assert results == ["ok"] * 8
    assert peak == 1


@pytest.mark.asyncio
async def test_higher_priority_drains_first():
    """Registration (CRITICAL) must not starve behind user searches (LOW)."""
    queue = DynadotQueue(max_per_minute=6000, concurrency=1)
    await queue.start()
    order: list[str] = []

    async def make(label: str):
        await asyncio.sleep(0.01)
        order.append(label)

    try:
        # Occupy the worker so the rest queue up behind it.
        blocker = asyncio.create_task(
            queue.submit(lambda: make("blocker"), priority=Priority.NORMAL)
        )
        await asyncio.sleep(0.005)

        tasks = [
            asyncio.create_task(queue.submit(lambda: make("low"), priority=Priority.LOW)),
            asyncio.create_task(
                queue.submit(lambda: make("critical"), priority=Priority.CRITICAL)
            ),
            asyncio.create_task(
                queue.submit(lambda: make("high"), priority=Priority.HIGH)
            ),
        ]
        await asyncio.gather(blocker, *tasks)
    finally:
        await queue.stop()

    assert order[0] == "blocker"
    assert order[1:] == ["critical", "high", "low"]


@pytest.mark.asyncio
async def test_exceptions_propagate_to_the_caller():
    queue = DynadotQueue(max_per_minute=6000, concurrency=1)
    await queue.start()

    async def boom():
        raise ValueError("kaboom")

    try:
        with pytest.raises(ValueError, match="kaboom"):
            await queue.submit(boom)
        # A failed task must not kill the worker.
        assert await queue.submit(lambda: _ok()) == "fine"
    finally:
        await queue.stop()


async def _ok():
    return "fine"


@pytest.mark.asyncio
async def test_submit_without_start_falls_back_to_direct_call():
    queue = DynadotQueue(max_per_minute=6000, concurrency=1)
    assert await queue.submit(_ok) == "fine"


@pytest.mark.asyncio
async def test_rate_limiter_blocks_beyond_the_window():
    """The 3rd call in a 2-per-minute window must wait."""
    limiter = RateLimiter(max_per_minute=2)
    await limiter.acquire()
    await limiter.acquire()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(limiter.acquire(), timeout=0.2)


@pytest.mark.asyncio
async def test_pending_reports_queue_depth():
    queue = DynadotQueue(max_per_minute=6000, concurrency=1)
    await queue.start()

    async def slow():
        await asyncio.sleep(0.05)

    try:
        tasks = [asyncio.create_task(queue.submit(slow)) for _ in range(4)]
        await asyncio.sleep(0.01)
        assert queue.pending >= 1
        await asyncio.gather(*tasks)
        assert queue.pending == 0
    finally:
        await queue.stop()
