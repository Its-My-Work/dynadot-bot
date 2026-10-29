"""Admin endpoints: pricing, orders, refund queue, task queue, API logs, stats.

These power the operator panel. Two things matter more than features here:

* **The refund queue must be impossible to miss.** ``REFUND_REQUIRED`` orders
  are money we owe a customer, so they get their own endpoint and their own
  headline stat rather than being filtered out of a generic order list.
* **A dead-lettered task must be requeueable.** When a registration gives up
  after a registrar outage, an operator needs one button to try again — not a
  database console.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import DbSession, Dynadot, Translate, require_admin
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import (
    AppSetting,
    Domain,
    DynadotApiLog,
    DynadotTask,
    Order,
    TldPricing,
    User,
)
from app.models.enums import DomainStatus, OrderStatus, TaskStatus
from app.schemas import (
    AdminLoginRequest,
    AdminLoginResponse,
    AdminOrderRead,
    AdminSettingsRead,
    AdminSettingsUpdate,
    AdminStats,
    ApiLogRead,
    RefundResolve,
    TaskRead,
    TldPricingRead,
    TldPricingUpdate,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])
auth_router = APIRouter(dependencies=[Depends(require_admin)])


@router.post("/login", response_model=AdminLoginResponse)
async def admin_login(payload: AdminLoginRequest) -> AdminLoginResponse:
    """Validate administrator credentials and return an authorization token."""
    settings = get_settings()
    if settings.admin_username and settings.admin_password:
        import secrets

        valid_user = secrets.compare_digest(
            payload.username.encode("utf8"), settings.admin_username.encode("utf8")
        )
        valid_pass = secrets.compare_digest(
            payload.password.encode("utf8"),
            settings.admin_password.get_secret_value().encode("utf8"),
        )
        if not (valid_user and valid_pass):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный логин или пароль",
            )
    import base64

    token = base64.b64encode(f"{payload.username}:{payload.password}".encode("utf8")).decode("utf8")
    return AdminLoginResponse(status="ok", username=payload.username, token=token)


# ------------------------------------------------------------------ pricing


@auth_router.get("/tld-pricing", response_model=list[TldPricingRead])
async def list_tld_pricing(session: DbSession) -> list[TldPricing]:
    result = await session.scalars(
        select(TldPricing).order_by(TldPricing.sort_order, TldPricing.tld)
    )
    return list(result)


@auth_router.patch("/tld-pricing/{tld}", response_model=TldPricingRead)
async def update_tld_pricing(
    tld: str,
    payload: TldPricingUpdate,
    session: DbSession,
    t: Translate,
) -> TldPricing:
    """Adjust the resale markup for one zone.

    Existing orders are unaffected: their price was frozen at checkout, so a
    markup change never retroactively alters what a customer owes.
    """
    pricing = await session.scalar(
        select(TldPricing).where(TldPricing.tld == tld.lstrip("."))
    )
    if pricing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(pricing, field, value)

    await session.commit()
    await session.refresh(pricing)
    return pricing


# ------------------------------------------------------------------- orders


@auth_router.get("/orders", response_model=list[AdminOrderRead])
async def list_orders(
    session: DbSession,
    order_status: OrderStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Order]:
    """All orders, newest first, optionally filtered by status."""
    stmt = select(Order).order_by(Order.created_at.desc())
    if order_status is not None:
        stmt = stmt.where(Order.status == order_status)
    result = await session.scalars(stmt.limit(limit).offset(offset))
    return list(result)


@auth_router.get("/refunds", response_model=list[AdminOrderRead])
async def list_pending_refunds(session: DbSession) -> list[Order]:
    """Orders where we hold the customer's money but could not deliver.

    Oldest first — the longer one of these sits, the worse it is.
    """
    result = await session.scalars(
        select(Order)
        .where(Order.status == OrderStatus.REFUND_REQUIRED)
        .order_by(Order.created_at)
    )
    return list(result)


@auth_router.post("/refunds/{order_id}/resolve", response_model=AdminOrderRead)
async def resolve_refund(
    order_id: str,
    payload: RefundResolve,
    session: DbSession,
    t: Translate,
) -> Order:
    """Record that a refund has actually been paid out.

    Refunds are executed in the gateway's own console (Platega has no refund
    API), so this endpoint only closes the loop in our ledger — hence the
    explicit status guard rather than a blind update.
    """
    order = await session.scalar(select(Order).where(Order.id == order_id))
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )
    if order.status is not OrderStatus.REFUND_REQUIRED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"order is in status {order.status.value}, not refund_required",
        )

    order.status = OrderStatus.REFUNDED
    if payload.note:
        order.error_message = payload.note
    await session.commit()
    await session.refresh(order)

    logger.info("refund resolved", extra={"order_id": order.id})
    return order


# -------------------------------------------------------------------- tasks


@auth_router.get("/tasks", response_model=list[TaskRead])
async def list_tasks(
    session: DbSession,
    task_status: TaskStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[DynadotTask]:
    """The Dynadot task queue, newest first."""
    stmt = select(DynadotTask).order_by(DynadotTask.created_at.desc())
    if task_status is not None:
        stmt = stmt.where(DynadotTask.status == task_status)
    result = await session.scalars(stmt.limit(limit))
    return list(result)


@auth_router.post("/tasks/{task_id}/retry", response_model=TaskRead)
async def retry_task(task_id: str, session: DbSession, t: Translate) -> DynadotTask:
    """Requeue a failed or dead-lettered task.

    The attempt budget is extended rather than reset so the task's history
    stays auditable, and the order is pulled back to PAID so the worker's
    claim can succeed.
    """
    task = await session.scalar(select(DynadotTask).where(DynadotTask.id == task_id))
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )
    if task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"task is already {task.status.value}",
        )

    task.status = TaskStatus.QUEUED
    task.max_attempts = task.attempts + 3
    task.scheduled_at = datetime.now(UTC)
    task.finished_at = None
    task.last_error = None

    if task.order_id:
        order = await session.scalar(select(Order).where(Order.id == task.order_id))
        # Only a stuck order is revived; a COMPLETED or REFUNDED one must not
        # be dragged back into the registration pipeline.
        if order is not None and order.status in {
            OrderStatus.FAILED,
            OrderStatus.REFUND_REQUIRED,
            OrderStatus.REGISTERING,
        }:
            order.status = OrderStatus.PAID

    await session.commit()
    await session.refresh(task)

    logger.info("task requeued by admin", extra={"task_id": task.id})
    return task


# --------------------------------------------------------------------- logs


@auth_router.get("/api-logs", response_model=list[ApiLogRead])
async def list_api_logs(
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[DynadotApiLog]:
    """Most recent Dynadot calls, newest first."""
    result = await session.scalars(
        select(DynadotApiLog)
        .order_by(DynadotApiLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result)


# -------------------------------------------------------------------- stats


@auth_router.get("/stats", response_model=AdminStats)
async def stats(session: DbSession) -> AdminStats:
    """Dashboard headline numbers.

    Revenue counts only COMPLETED orders: billing a refund-pending order as
    revenue would overstate the books by exactly the amount we owe back.
    """

    async def count(stmt) -> int:
        return int(await session.scalar(stmt) or 0)

    orders_by_status = {
        row[0]: row[1]
        for row in (
            await session.execute(
                select(Order.status, func.count(Order.id)).group_by(Order.status)
            )
        ).all()
    }
    tasks_by_status = {
        row[0]: row[1]
        for row in (
            await session.execute(
                select(DynadotTask.status, func.count(DynadotTask.id)).group_by(
                    DynadotTask.status
                )
            )
        ).all()
    }

    revenue = await session.scalar(
        select(func.coalesce(func.sum(Order.price_rub), 0)).where(
            Order.status == OrderStatus.COMPLETED
        )
    )

    return AdminStats(
        users=await count(select(func.count(User.id))),
        orders_total=sum(orders_by_status.values()),
        orders_completed=orders_by_status.get(OrderStatus.COMPLETED, 0),
        orders_awaiting_payment=orders_by_status.get(OrderStatus.AWAITING_PAYMENT, 0),
        refunds_pending=orders_by_status.get(OrderStatus.REFUND_REQUIRED, 0),
        domains_active=await count(
            select(func.count(Domain.id)).where(Domain.status == DomainStatus.ACTIVE)
        ),
        revenue_rub=float(revenue or 0),
        tasks_queued=tasks_by_status.get(TaskStatus.QUEUED, 0),
        tasks_dead_letter=tasks_by_status.get(TaskStatus.DEAD_LETTER, 0),
    )


@auth_router.get("/queue-status")
async def queue_status(dynadot: Dynadot) -> dict[str, int | bool]:
    """Depth of the serialising Dynadot queue — key operational signal."""
    return {"pending": dynadot.queue.pending}


# ------------------------------------------------------------------ settings


@auth_router.get("/settings", response_model=AdminSettingsRead)
async def get_admin_settings(session: DbSession) -> AdminSettingsRead:
    """Retrieve runtime settings combining environment defaults and database overrides."""
    settings = get_settings()
    rows = (await session.scalars(select(AppSetting))).all()
    db_map = {r.key: r.value for r in rows if r.value is not None}

    return AdminSettingsRead(
        usd_to_rub_rate=float(db_map.get("usd_to_rub_rate", settings.usd_to_rub_rate)),
        default_markup_percent=float(
            db_map.get("default_markup_percent", settings.default_markup_percent)
        ),
        dynadot_base_url=str(db_map.get("dynadot_base_url", settings.dynadot_base_url)),
        dynadot_configured=settings.dynadot_configured,
        platega_configured=settings.platega_configured,
        registration_worker_enabled=db_map.get(
            "registration_worker_enabled", str(settings.registration_worker_enabled)
        ).lower()
        in ("true", "1", "yes"),
        environment=settings.environment,
        app_version="0.1.0",
    )


@auth_router.patch("/settings", response_model=AdminSettingsRead)
async def update_admin_settings(
    payload: AdminSettingsUpdate, session: DbSession
) -> AdminSettingsRead:
    """Update runtime settings and persist overrides to PostgreSQL."""
    settings = get_settings()

    async def _upsert(key: str, val: str, desc: str) -> None:
        row = await session.get(AppSetting, key)
        if not row:
            session.add(AppSetting(key=key, value=val, description=desc))
        else:
            row.value = val

    if payload.usd_to_rub_rate is not None:
        await _upsert("usd_to_rub_rate", str(payload.usd_to_rub_rate), "USD to RUB rate")
        settings.usd_to_rub_rate = payload.usd_to_rub_rate

    if payload.default_markup_percent is not None:
        await _upsert(
            "default_markup_percent",
            str(payload.default_markup_percent),
            "Default markup percent",
        )
        settings.default_markup_percent = payload.default_markup_percent

    if payload.dynadot_base_url is not None:
        await _upsert("dynadot_base_url", str(payload.dynadot_base_url), "Dynadot API base URL")
        settings.dynadot_base_url = payload.dynadot_base_url

    if payload.registration_worker_enabled is not None:
        await _upsert(
            "registration_worker_enabled",
            str(payload.registration_worker_enabled).lower(),
            "Registration worker enabled",
        )
        settings.registration_worker_enabled = payload.registration_worker_enabled

    await session.commit()
    return await get_admin_settings(session)


router.include_router(auth_router)
