"""Background worker that turns paid orders into registered domains.

Why a durable DB-backed worker instead of an in-process callback on the payment
webhook:

* A registration must survive a restart. The webhook returns 200 the moment the
  money is confirmed; if the process dies a second later, a paid order would be
  lost forever with no record of what to do.
* Dynadot serialises requests (one at a time), so registration cannot simply be
  fired off concurrently from a request handler.
* Retries need state. Attempt counts, backoff and a dead-letter bucket all live
  on ``DynadotTask`` so the admin panel can show and requeue them.

The loop is deliberately single-flight and poll-based. There is no external
broker (no Redis/Celery) because PostgreSQL already gives the atomic claim this
needs, and one fewer moving part is worth more than millisecond latency on an
operation whose registrar call takes seconds anyway.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.integrations.dynadot.client import DynadotClient
from app.integrations.dynadot.signing import build_request_id
from app.models.audit import DynadotTask
from app.models.enums import OrderStatus, TaskStatus
from app.models.order import Order
from app.models.user import User
from app.services.registration import (
    RegistrationOutcome,
    RegistrationResult,
    RegistrationService,
    dump_json,
)

logger = get_logger(__name__)

#: The only operation the worker executes today. Stored as a string so renewals
#: and NS writes can join the same queue without a migration.
OP_REGISTER_DOMAIN = "register_domain"

#: Exponential backoff per attempt, in seconds, capped so a wedged registrar
#: does not push a paid order hours into the future.
BACKOFF_BASE_SECONDS = 15
BACKOFF_MAX_SECONDS = 600

#: A task stuck in RUNNING longer than this lost its worker (crash, SIGKILL)
#: and is safe to reclaim.
STALE_RUNNING_MINUTES = 15

#: How long `stop()` lets an in-flight registration finish before cancelling.
#: Comfortably longer than a normal Dynadot register call so a graceful
#: shutdown does not abort a purchase that already left money on the table.
SHUTDOWN_DRAIN_SECONDS = 30.0


def backoff_seconds(attempt: int) -> int:
    """Delay before attempt ``attempt`` (1-based)."""
    delay = BACKOFF_BASE_SECONDS * (2 ** max(0, attempt - 1))
    return min(delay, BACKOFF_MAX_SECONDS)


async def enqueue_registration(
    session: AsyncSession,
    order: Order,
    *,
    max_attempts: int = 4,
) -> DynadotTask:
    """Queue (or return the existing) registration task for ``order``.

    Idempotent by order id: the payment webhook can be redelivered any number
    of times and a single task results. The task's ``request_id`` is reused as
    the order's Dynadot idempotency key so a retry cannot double-register.
    """
    existing = await session.scalar(
        select(DynadotTask).where(
            DynadotTask.order_id == order.id,
            DynadotTask.operation == OP_REGISTER_DOMAIN,
            DynadotTask.status.in_(
                [TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.SUCCEEDED]
            ),
        )
    )
    if existing is not None:
        return existing

    if not order.dynadot_request_id:
        order.dynadot_request_id = build_request_id()

    task = DynadotTask(
        operation=OP_REGISTER_DOMAIN,
        priority=0,  # CRITICAL: money has already been taken.
        status=TaskStatus.QUEUED,
        request_id=order.dynadot_request_id,
        order_id=order.id,
        payload=dump_json({"domain": order.domain_name, "years": order.years}),
        max_attempts=max_attempts,
        scheduled_at=datetime.now(UTC),
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    logger.info(
        "registration task queued",
        extra={"order_id": order.id, "task_id": task.id},
    )
    return task


class RegistrationWorker:
    """Polls for due registration tasks and executes them one at a time."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client: DynadotClient,
        *,
        settings: Settings | None = None,
        bot: Any | None = None,
        poll_interval: float = 5.0,
    ) -> None:
        self._session_factory = session_factory
        self._client = client
        self._settings = settings or get_settings()
        self._bot = bot
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="registration-worker")
        logger.info("registration worker started")

    async def stop(self, *, drain_timeout: float = SHUTDOWN_DRAIN_SECONDS) -> None:
        """Signal the loop to finish and wait for the current task to land.

        Cancelling mid-flight is a last resort: a hard cancel can abort an
        in-progress registrar call after the domain was already bought,
        leaving the task stuck in RUNNING until the stale-reclaim window
        passes. So the loop is asked to stop and given `drain_timeout` to
        return between tasks; only a worker still busy after that is cancelled.
        """
        self._stop.set()
        task = self._task
        if task is None:
            logger.info("registration worker stopped")
            return

        self._task = None
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=drain_timeout)
        except TimeoutError:
            # Still mid-registration: cancel and accept the stale reclaim.
            logger.warning(
                "registration worker did not drain in time; cancelling",
                extra={"drain_timeout": drain_timeout},
            )
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("registration worker failed during shutdown")
        logger.info("registration worker stopped")

    async def _run(self) -> None:
        """Poll forever, never dying on a single bad task."""
        while not self._stop.is_set():
            try:
                processed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A crashed worker means paid orders stop being registered, so
                # every failure is swallowed and retried on the next tick.
                logger.exception("registration worker tick failed")
                processed = 0

            if processed == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self._poll_interval
                    )

    # ----------------------------------------------------------------- cycle

    async def run_once(self) -> int:
        """Execute at most one due task. Returns the number processed."""
        async with self._session_factory() as session:
            await self._reclaim_stale(session)
            await self._adopt_orphans(session)
            task = await self._claim_next(session)
            if task is None:
                return 0

        # A fresh session per task keeps a long registrar call off the
        # connection used for claiming, and guarantees the task's own failure
        # cannot roll back the claim.
        async with self._session_factory() as session:
            await self._execute(session, task.id)
        return 1

    async def _claim_next(self, session: AsyncSession) -> DynadotTask | None:
        """Atomically take the highest-priority due task.

        The conditional UPDATE is the claim; selecting then updating without
        the status guard would let two workers take the same task.
        """
        now = datetime.now(UTC)
        candidate = await session.scalar(
            select(DynadotTask)
            .where(
                DynadotTask.status == TaskStatus.QUEUED,
                or_(
                    DynadotTask.scheduled_at.is_(None),
                    DynadotTask.scheduled_at <= now,
                ),
            )
            .order_by(DynadotTask.priority, DynadotTask.created_at)
            .limit(1)
        )
        if candidate is None:
            return None

        result = await session.execute(
            update(DynadotTask)
            .where(
                DynadotTask.id == candidate.id,
                DynadotTask.status == TaskStatus.QUEUED,
            )
            .values(
                status=TaskStatus.RUNNING,
                started_at=now,
                attempts=DynadotTask.attempts + 1,
            )
        )
        if result.rowcount == 0:
            await session.rollback()
            return None  # lost the race; next tick picks up whatever is left

        await session.commit()
        await session.refresh(candidate)
        return candidate

    async def _reclaim_stale(self, session: AsyncSession) -> None:
        """Return tasks whose worker died back to the queue."""
        cutoff = datetime.now(UTC) - timedelta(minutes=STALE_RUNNING_MINUTES)
        result = await session.execute(
            update(DynadotTask)
            .where(
                DynadotTask.status == TaskStatus.RUNNING,
                DynadotTask.started_at.isnot(None),
                DynadotTask.started_at < cutoff,
            )
            .values(status=TaskStatus.QUEUED, scheduled_at=datetime.now(UTC))
        )
        if result.rowcount:
            await session.commit()
            logger.warning("reclaimed stale tasks", extra={"count": result.rowcount})
        else:
            await session.rollback()

    async def _adopt_orphans(self, session: AsyncSession) -> None:
        """Queue PAID orders that have no task.

        This is the safety net that makes a lost webhook survivable: if the
        process died between confirming payment and enqueueing, the order is
        still PAID in the database and gets picked up here.
        """
        orders = list(
            await session.scalars(
                select(Order)
                .outerjoin(
                    DynadotTask,
                    (DynadotTask.order_id == Order.id)
                    & (DynadotTask.operation == OP_REGISTER_DOMAIN),
                )
                .where(Order.status == OrderStatus.PAID, DynadotTask.id.is_(None))
                .limit(20)
            )
        )
        for order in orders:
            await enqueue_registration(session, order)
            logger.info("adopted orphan paid order", extra={"order_id": order.id})

    # --------------------------------------------------------------- execute

    async def _execute(self, session: AsyncSession, task_id: str) -> None:
        task = await session.scalar(
            select(DynadotTask).where(DynadotTask.id == task_id)
        )
        if task is None or not task.order_id:
            return

        service = RegistrationService(session, self._client, self._settings)
        try:
            result = await service.register_order(task.order_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("registration task raised", extra={"task_id": task.id})
            await self._fail_task(session, task, str(exc))
            return

        await self._record(session, task, result)
        await self._notify(session, result)

    async def _record(
        self, session: AsyncSession, task: DynadotTask, result: RegistrationResult
    ) -> None:
        """Persist the attempt's outcome and schedule a retry if needed."""
        now = datetime.now(UTC)
        task.result = dump_json(
            {"outcome": result.outcome, "detail": result.detail}
        )

        if result.outcome in (
            RegistrationOutcome.COMPLETED,
            RegistrationOutcome.SKIPPED,
        ):
            task.status = TaskStatus.SUCCEEDED
            task.finished_at = now
        elif result.outcome in (
            RegistrationOutcome.REFUND_REQUIRED,
            RegistrationOutcome.FAILED,
        ):
            # A terminal business outcome: retrying would not change it and the
            # order already carries the operator-visible status.
            task.status = TaskStatus.FAILED
            task.finished_at = now
            task.last_error = result.detail
        else:  # RETRY
            task.last_error = result.detail
            if task.attempts >= task.max_attempts:
                task.status = TaskStatus.DEAD_LETTER
                task.finished_at = now
                # Retries are exhausted but the customer's money is still with
                # us, so this must land in the refund queue, not be forgotten.
                await RegistrationService(
                    session, self._client, self._settings
                ).escalate_to_refund(
                    task.order_id or "",
                    result.detail or "registration retries exhausted",
                )
            else:
                task.status = TaskStatus.QUEUED
                task.scheduled_at = now + timedelta(
                    seconds=backoff_seconds(task.attempts)
                )

        await session.commit()
        logger.info(
            "registration task finished",
            extra={
                "task_id": task.id,
                "task_status": task.status.value,
                "outcome": result.outcome,
                "attempts": task.attempts,
            },
        )

    async def _fail_task(
        self, session: AsyncSession, task: DynadotTask, error: str
    ) -> None:
        task.status = TaskStatus.FAILED
        task.finished_at = datetime.now(UTC)
        task.last_error = error[:1000]
        await session.commit()

    async def _notify(self, session: AsyncSession, result: RegistrationResult) -> None:
        """Tell the customer what happened, in their language.

        Notification failures are swallowed: a blocked bot must never undo a
        completed registration.
        """
        if self._bot is None:
            return
        if result.outcome not in (
            RegistrationOutcome.COMPLETED,
            RegistrationOutcome.REFUND_REQUIRED,
            RegistrationOutcome.FAILED,
        ):
            return

        order = await session.scalar(
            select(Order).where(Order.id == result.order_id)
        )
        if order is None:
            return
        user = await session.scalar(select(User).where(User.id == order.user_id))
        if user is None:
            return

        from app.bot.notifications import notify_order_status

        try:
            await notify_order_status(
                self._bot,
                user.telegram_id,
                language=user.language.value,
                status=order.status,
                domain=order.domain_name,
            )
        except Exception:
            logger.exception("order notification failed", extra={"order_id": order.id})


__all__ = [
    "BACKOFF_MAX_SECONDS",
    "OP_REGISTER_DOMAIN",
    "SHUTDOWN_DRAIN_SECONDS",
    "STALE_RUNNING_MINUTES",
    "RegistrationWorker",
    "backoff_seconds",
    "enqueue_registration",
]
