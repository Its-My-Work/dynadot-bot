"""Tests for the background registration worker.

The worker is the component that must not lose a paid order, so the tests here
target survival properties: idempotent enqueue, atomic claiming, retry with
backoff, dead-lettering into the refund queue, stale-task reclaim and the
orphan sweep that rescues orders whose enqueue never happened.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.integrations.dynadot.exceptions import (
    DomainUnavailableError,
    DynadotTransportError,
)
from app.models import Base
from app.models.audit import DynadotTask
from app.models.domain import Domain
from app.models.enums import OrderStatus, TaskStatus
from app.models.order import Order
from app.models.user import User
from app.services.worker import (
    BACKOFF_MAX_SECONDS,
    OP_REGISTER_DOMAIN,
    STALE_RUNNING_MINUTES,
    RegistrationWorker,
    backoff_seconds,
    enqueue_registration,
)
from tests.test_registration_service import FakeDynadot


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    # A file-backed database, not `:memory:`: the worker opens a second
    # connection from its own task, and each in-memory connection would get
    # its own empty database.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def settings():
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        dynadot_api_key="k",
        dynadot_api_secret="s",
    )


async def seed_order(
    session_factory,
    *,
    status: OrderStatus = OrderStatus.PAID,
    domain: str = "worker-test.com",
    language: str = "ru",
) -> str:
    async with session_factory() as session:
        user = User(telegram_id=abs(hash(domain)) % 10**9, username="buyer")
        session.add(user)
        await session.flush()
        order = Order(
            user_id=user.id,
            domain_name=domain,
            tld=domain.rpartition(".")[2],
            years=1,
            status=status,
            cost_usd=Decimal("10"),
            price_rub=Decimal("1200"),
        )
        session.add(order)
        await session.commit()
        return order.id


# -------------------------------------------------------------------- backoff


def test_backoff_grows_exponentially_and_is_capped():
    assert backoff_seconds(1) == 15
    assert backoff_seconds(2) == 30
    assert backoff_seconds(3) == 60
    # A wedged registrar must not push a paid order hours out.
    assert backoff_seconds(50) == BACKOFF_MAX_SECONDS


# ------------------------------------------------------------------- enqueue


@pytest.mark.asyncio
async def test_enqueue_creates_a_critical_priority_task(session_factory):
    order_id = await seed_order(session_factory)
    async with session_factory() as session:
        order = await session.get(Order, order_id)
        task = await enqueue_registration(session, order)

        assert task.operation == OP_REGISTER_DOMAIN
        assert task.status is TaskStatus.QUEUED
        # Priority 0: money is already taken, this outranks search.
        assert task.priority == 0
        assert task.order_id == order_id
        assert task.request_id == order.dynadot_request_id


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_for_redelivered_webhooks(session_factory):
    """Platega redelivers CONFIRMED; two tasks would mean two registrations."""
    order_id = await seed_order(session_factory)
    async with session_factory() as session:
        order = await session.get(Order, order_id)
        first = await enqueue_registration(session, order)
        second = await enqueue_registration(session, order)

        assert first.id == second.id
        all_tasks = list(
            await session.scalars(
                select(DynadotTask).where(DynadotTask.order_id == order_id)
            )
        )
        assert len(all_tasks) == 1


@pytest.mark.asyncio
async def test_enqueue_reuses_the_orders_existing_request_id(session_factory):
    order_id = await seed_order(session_factory)
    async with session_factory() as session:
        order = await session.get(Order, order_id)
        order.dynadot_request_id = "fixed-request-id"
        await session.commit()

        task = await enqueue_registration(session, order)
        assert task.request_id == "fixed-request-id"


# --------------------------------------------------------------- happy path


@pytest.mark.asyncio
async def test_run_once_registers_a_paid_order_and_succeeds_the_task(
    session_factory, settings
):
    order_id = await seed_order(session_factory, domain="happy.com")
    client = FakeDynadot()
    worker = RegistrationWorker(session_factory, client, settings=settings)

    processed = await worker.run_once()

    assert processed == 1
    async with session_factory() as session:
        order = await session.get(Order, order_id)
        assert order.status is OrderStatus.COMPLETED
        task = await session.scalar(
            select(DynadotTask).where(DynadotTask.order_id == order_id)
        )
        assert task.status is TaskStatus.SUCCEEDED
        assert task.attempts == 1
        assert task.finished_at is not None
        domain = await session.scalar(select(Domain).where(Domain.name == "happy.com"))
        assert domain is not None


@pytest.mark.asyncio
async def test_run_once_is_a_noop_when_the_queue_is_empty(session_factory, settings):
    worker = RegistrationWorker(session_factory, FakeDynadot(), settings=settings)
    assert await worker.run_once() == 0


@pytest.mark.asyncio
async def test_a_future_scheduled_task_is_not_picked_up(session_factory, settings):
    """Backoff must actually delay the retry, not be ignored by the claim."""
    order_id = await seed_order(session_factory, domain="later.com")
    async with session_factory() as session:
        order = await session.get(Order, order_id)
        task = await enqueue_registration(session, order)
        task.scheduled_at = datetime.now(UTC) + timedelta(minutes=10)
        await session.commit()

    worker = RegistrationWorker(session_factory, FakeDynadot(), settings=settings)
    assert await worker.run_once() == 0


# ---------------------------------------------------------------- orphans


@pytest.mark.asyncio
async def test_orphan_paid_order_is_adopted(session_factory, settings):
    """The webhook may have died before enqueueing; the order is still PAID."""
    order_id = await seed_order(session_factory, domain="orphan.com")
    worker = RegistrationWorker(session_factory, FakeDynadot(), settings=settings)

    processed = await worker.run_once()

    assert processed == 1
    async with session_factory() as session:
        order = await session.get(Order, order_id)
        assert order.status is OrderStatus.COMPLETED


@pytest.mark.asyncio
async def test_unpaid_orders_are_never_adopted(session_factory, settings):
    await seed_order(
        session_factory, status=OrderStatus.AWAITING_PAYMENT, domain="unpaid.com"
    )
    worker = RegistrationWorker(session_factory, FakeDynadot(), settings=settings)

    assert await worker.run_once() == 0
    async with session_factory() as session:
        assert (await session.scalar(select(DynadotTask).limit(1))) is None


# ------------------------------------------------------------------- refunds


@pytest.mark.asyncio
async def test_taken_domain_fails_the_task_and_queues_a_refund(session_factory, settings):
    order_id = await seed_order(session_factory, domain="taken.com")
    client = FakeDynadot(available=False)
    worker = RegistrationWorker(session_factory, client, settings=settings)

    await worker.run_once()

    async with session_factory() as session:
        order = await session.get(Order, order_id)
        assert order.status is OrderStatus.REFUND_REQUIRED
        task = await session.scalar(
            select(DynadotTask).where(DynadotTask.order_id == order_id)
        )
        # Terminal: retrying a taken domain can never succeed.
        assert task.status is TaskStatus.FAILED
        assert task.attempts == 1


# --------------------------------------------------------------- retry path


@pytest.mark.asyncio
async def test_transient_failure_requeues_with_a_future_schedule(
    session_factory, settings
):
    order_id = await seed_order(session_factory, domain="retrylater.com")
    client = FakeDynadot(register_error=DynadotTransportError("timeout"))
    worker = RegistrationWorker(session_factory, client, settings=settings)

    await worker.run_once()

    async with session_factory() as session:
        order = await session.get(Order, order_id)
        # Back to PAID so the next claim can take it.
        assert order.status is OrderStatus.PAID
        task = await session.scalar(
            select(DynadotTask).where(DynadotTask.order_id == order_id)
        )
        assert task.status is TaskStatus.QUEUED
        assert task.attempts == 1
        # The retry is genuinely deferred, not immediately due again.
        assert task.scheduled_at is not None
        scheduled = task.scheduled_at
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=UTC)
        assert scheduled > datetime.now(UTC)


@pytest.mark.asyncio
async def test_retry_eventually_succeeds_without_a_second_request_id(
    session_factory, settings
):
    order_id = await seed_order(session_factory, domain="eventual.com")
    client = FakeDynadot(register_error=DynadotTransportError("502"))
    worker = RegistrationWorker(session_factory, client, settings=settings)

    await worker.run_once()

    # Clear the backoff so the retry is due, then let it succeed.
    async with session_factory() as session:
        task = await session.scalar(
            select(DynadotTask).where(DynadotTask.order_id == order_id)
        )
        task.scheduled_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    client.register_error = None

    assert await worker.run_once() == 1

    async with session_factory() as session:
        order = await session.get(Order, order_id)
        assert order.status is OrderStatus.COMPLETED
        task = await session.scalar(
            select(DynadotTask).where(DynadotTask.order_id == order_id)
        )
        assert task.status is TaskStatus.SUCCEEDED
        assert task.attempts == 2

    # One stable idempotency key across both attempts.
    assert len({c["request_id"] for c in client.register_calls}) == 1


@pytest.mark.asyncio
async def test_exhausted_retries_dead_letter_and_escalate_to_refund(
    session_factory, settings
):
    """Giving up must still return the money, never silently drop the order."""
    order_id = await seed_order(session_factory, domain="deadletter.com")
    client = FakeDynadot(register_error=DynadotTransportError("always down"))
    worker = RegistrationWorker(session_factory, client, settings=settings)

    async with session_factory() as session:
        order = await session.get(Order, order_id)
        task = await enqueue_registration(session, order, max_attempts=2)
        task_id = task.id

    for _ in range(2):
        async with session_factory() as session:
            task = await session.get(DynadotTask, task_id)
            task.scheduled_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        await worker.run_once()

    async with session_factory() as session:
        task = await session.get(DynadotTask, task_id)
        assert task.status is TaskStatus.DEAD_LETTER
        assert task.attempts == 2
        order = await session.get(Order, order_id)
        assert order.status is OrderStatus.REFUND_REQUIRED


# ---------------------------------------------------------- stale reclaiming


@pytest.mark.asyncio
async def test_stale_running_task_is_reclaimed(session_factory, settings):
    """A SIGKILLed worker leaves RUNNING rows that nobody would ever finish."""
    order_id = await seed_order(session_factory, domain="stale.com")
    async with session_factory() as session:
        order = await session.get(Order, order_id)
        task = await enqueue_registration(session, order)
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(UTC) - timedelta(minutes=STALE_RUNNING_MINUTES + 5)
        await session.commit()
        task_id = task.id

    worker = RegistrationWorker(session_factory, FakeDynadot(), settings=settings)
    processed = await worker.run_once()

    assert processed == 1
    async with session_factory() as session:
        task = await session.get(DynadotTask, task_id)
        assert task.status is TaskStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_a_freshly_running_task_is_left_alone(session_factory, settings):
    """Reclaiming too eagerly would double-register a live task."""
    order_id = await seed_order(session_factory, domain="inflight.com")
    async with session_factory() as session:
        order = await session.get(Order, order_id)
        task = await enqueue_registration(session, order)
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(UTC)
        await session.commit()

    worker = RegistrationWorker(session_factory, FakeDynadot(), settings=settings)
    assert await worker.run_once() == 0


# ------------------------------------------------------------- notifications


@pytest.mark.asyncio
async def test_customer_is_notified_in_their_language_on_completion(
    session_factory, settings
):
    await seed_order(session_factory, domain="notify.com")
    sent: list[tuple[int, str]] = []

    class FakeBot:
        async def send_message(self, chat_id, text, reply_markup=None):
            sent.append((chat_id, text))

    worker = RegistrationWorker(
        session_factory, FakeDynadot(), settings=settings, bot=FakeBot()
    )
    await worker.run_once()

    assert len(sent) == 1
    assert "notify.com" in sent[0][1]


@pytest.mark.asyncio
async def test_a_blocked_bot_does_not_undo_a_registration(session_factory, settings):
    """Notification failure must never roll back a completed registration."""
    order_id = await seed_order(session_factory, domain="blocked.com")

    class ExplodingBot:
        async def send_message(self, *args, **kwargs):
            raise RuntimeError("bot blocked by user")

    worker = RegistrationWorker(
        session_factory, FakeDynadot(), settings=settings, bot=ExplodingBot()
    )
    await worker.run_once()

    async with session_factory() as session:
        order = await session.get(Order, order_id)
        assert order.status is OrderStatus.COMPLETED


# ------------------------------------------------------------------ lifecycle


@pytest.mark.asyncio
async def test_worker_start_and_stop_are_idempotent(session_factory, settings):
    worker = RegistrationWorker(
        session_factory, FakeDynadot(), settings=settings, poll_interval=0.01
    )
    await worker.start()
    await worker.start()  # second start must not spawn a second loop
    await asyncio.sleep(0.05)
    await worker.stop()
    await worker.stop()


@pytest.mark.asyncio
async def test_running_loop_drains_a_task_it_finds(session_factory, settings):
    order_id = await seed_order(session_factory, domain="looped.com")
    worker = RegistrationWorker(
        session_factory, FakeDynadot(), settings=settings, poll_interval=0.01
    )
    await worker.start()
    try:
        for _ in range(100):
            async with session_factory() as session:
                order = await session.get(Order, order_id)
                if order.status is OrderStatus.COMPLETED:
                    break
            await asyncio.sleep(0.02)
    finally:
        await worker.stop()

    async with session_factory() as session:
        order = await session.get(Order, order_id)
        assert order.status is OrderStatus.COMPLETED


@pytest.mark.asyncio
async def test_a_raising_tick_does_not_kill_the_loop(session_factory, settings):
    """A dead worker means paid orders stop registering — it must self-heal."""
    worker = RegistrationWorker(
        session_factory, FakeDynadot(), settings=settings, poll_interval=0.01
    )
    calls = {"n": 0}
    original = worker.run_once

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient db blip")
        return await original()

    worker.run_once = flaky
    await worker.start()
    await asyncio.sleep(0.1)
    await worker.stop()

    assert calls["n"] > 1


@pytest.mark.asyncio
async def test_stop_waits_for_an_in_flight_task_instead_of_cancelling(
    session_factory, settings
):
    """A hard cancel mid-registration can strand a RUNNING task (and a paid
    order) until the stale-reclaim window, so stop() must drain first."""
    order_id = await seed_order(session_factory, domain="draining.com")
    worker = RegistrationWorker(
        session_factory, FakeDynadot(), settings=settings, poll_interval=0.01
    )

    started = asyncio.Event()
    release = asyncio.Event()
    original = worker.run_once

    async def slow_once():
        started.set()
        await release.wait()
        return await original()

    worker.run_once = slow_once
    await worker.start()
    await asyncio.wait_for(started.wait(), timeout=1)

    # stop() is called while the tick is mid-flight; it must not cancel it.
    stopping = asyncio.create_task(worker.stop(drain_timeout=5))
    await asyncio.sleep(0.05)
    assert not stopping.done(), "stop() returned before the task drained"

    release.set()
    await asyncio.wait_for(stopping, timeout=5)

    async with session_factory() as session:
        order = await session.get(Order, order_id)
        assert order.status is OrderStatus.COMPLETED


@pytest.mark.asyncio
async def test_stop_cancels_a_task_that_refuses_to_drain(session_factory, settings):
    """Shutdown must still terminate if a tick hangs forever."""
    worker = RegistrationWorker(
        session_factory, FakeDynadot(), settings=settings, poll_interval=0.01
    )

    started = asyncio.Event()

    async def hanging_once():
        started.set()
        await asyncio.Event().wait()  # never completes
        return 0

    worker.run_once = hanging_once
    await worker.start()
    await asyncio.wait_for(started.wait(), timeout=1)

    await asyncio.wait_for(worker.stop(drain_timeout=0.05), timeout=5)


@pytest.mark.asyncio
async def test_unavailable_raised_from_search_is_handled(session_factory, settings):
    order_id = await seed_order(session_factory, domain="searchgone.com")
    client = FakeDynadot(search_error=DomainUnavailableError("gone", code="domain_taken"))
    worker = RegistrationWorker(session_factory, client, settings=settings)

    await worker.run_once()

    async with session_factory() as session:
        order = await session.get(Order, order_id)
        assert order.status is OrderStatus.REFUND_REQUIRED
