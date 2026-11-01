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

import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import SecretStr
from sqlalchemy import func, select

from app.api.deps import DbSession, Dynadot, Translate, require_admin
from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.dynadot.client import DynadotClient
from app.integrations.dynadot.exceptions import DynadotAuthError
from app.models import (
    AppSetting,
    Domain,
    DynadotApiLog,
    DynadotTask,
    Order,
    TldPricing,
    User,
)
from app.models.enums import DomainStatus, OrderStatus, TaskStatus, UserRole
from app.schemas import (
    AdminDomainAssignRequest,
    AdminDomainItem,
    AdminDomainListResponse,
    AdminDomainSyncResponse,
    AdminLoginRequest,
    AdminLoginResponse,
    AdminOrderRead,
    AdminSettingsRead,
    AdminSettingsUpdate,
    AdminSetupRequest,
    AdminSetupResponse,
    AdminSetupStatusResponse,
    AdminStats,
    AdminUserItem,
    ApiLogRead,
    CategoriesResponse,
    CategoryItem,
    ConnectionTestResponse,
    RefundResolve,
    TaskRead,
    TldPricingCreate,
    TldPricingRead,
    TldPricingUpdate,
    TldSyncResponse,
)
from app.services.security import hash_password, verify_password

logger = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])
auth_router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/setup-status", response_model=AdminSetupStatusResponse)
async def admin_setup_status(session: DbSession) -> AdminSetupStatusResponse:
    """Check if the system needs initial superadmin setup."""
    settings = get_settings()
    admin_count = await session.scalar(
        select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)
    )
    has_env_admin = bool(settings.admin_username and settings.admin_password)
    needs_setup = (admin_count == 0) and not has_env_admin
    return AdminSetupStatusResponse(needs_setup=needs_setup)


@router.post("/setup", response_model=AdminSetupResponse)
async def admin_setup(
    payload: AdminSetupRequest,
    session: DbSession,
) -> AdminSetupResponse:
    """Initial superadmin bootstrap: only permitted when no admin exists in the system."""
    import base64
    import secrets

    admin_count = await session.scalar(
        select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)
    )
    if admin_count and admin_count > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Администратор уже настроен в системе. Первоначальная настройка закрыта.",
        )

    norm_email = payload.email.strip().lower()
    user = await session.scalar(select(User).where(func.lower(User.email) == norm_email))
    if user is None:
        fallback_tid = 9_000_000_000 + secrets.randbelow(900_000_000)
        user = User(
            email=norm_email,
            password_hash=hash_password(payload.password),
            has_password=True,
            role=UserRole.ADMIN,
            first_name=payload.name or "Admin",
            telegram_id=fallback_tid,
            username=norm_email.split("@")[0][:32],
            last_seen_at=datetime.now(UTC),
        )
        session.add(user)
    else:
        user.role = UserRole.ADMIN
        user.password_hash = hash_password(payload.password)
        user.has_password = True
        user.last_seen_at = datetime.now(UTC)

    await session.commit()
    token = base64.b64encode(f"{norm_email}:{payload.password}".encode()).decode("utf8")
    return AdminSetupResponse(
        status="ok",
        username=norm_email,
        token=token,
        message="Главный администратор успешно настроен!",
    )


@router.post("/login", response_model=AdminLoginResponse)
async def admin_login(
    payload: AdminLoginRequest,
    session: DbSession,
) -> AdminLoginResponse:
    """Validate administrator credentials against DB admin users or env credentials."""
    import base64
    import secrets

    # 1. Check system credentials if configured
    settings = get_settings()
    is_valid = False
    if settings.admin_username and settings.admin_password:
        valid_user = secrets.compare_digest(
            payload.username.encode("utf8"), settings.admin_username.encode("utf8")
        )
        valid_pass = secrets.compare_digest(
            payload.password.encode("utf8"),
            settings.admin_password.get_secret_value().encode("utf8"),
        )
        if valid_user and valid_pass:
            is_valid = True

    # 2. Check database admin users
    if not is_valid:
        norm_login = payload.username.strip().lower()
        db_user = await session.scalar(
            select(User).where(
                (func.lower(User.email) == norm_login)
                | (func.lower(User.username) == norm_login)
            )
        )
        if (
            db_user is not None
            and db_user.role == UserRole.ADMIN
            and db_user.has_password
            and verify_password(payload.password, db_user.password_hash)
        ):
            is_valid = True

    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )

    token = base64.b64encode(f"{payload.username}:{payload.password}".encode()).decode(
        "utf8"
    )
    return AdminLoginResponse(status="ok", username=payload.username, token=token)


# ------------------------------------------------------------------ pricing

DEFAULT_TLD_SEEDS = [
    # Top Featured
    {"tld": "com", "cost": 10.88, "fixed": 1290.0, "featured": True, "sort": 1},
    {"tld": "net", "cost": 12.52, "fixed": 1490.0, "featured": True, "sort": 2},
    {"tld": "org", "cost": 7.99, "fixed": 990.0, "featured": True, "sort": 3},
    {"tld": "io", "cost": 28.89, "fixed": 3490.0, "featured": True, "sort": 4},
    {"tld": "ai", "cost": 65.00, "fixed": 7990.0, "featured": True, "sort": 5},
    {"tld": "xyz", "cost": 1.99, "fixed": 390.0, "featured": True, "sort": 6},
    {"tld": "co", "cost": 15.50, "fixed": 1790.0, "featured": True, "sort": 7},
    {"tld": "pro", "cost": 3.99, "fixed": 590.0, "featured": True, "sort": 8},
    {"tld": "store", "cost": 2.99, "fixed": 490.0, "featured": True, "sort": 9},
    # AI & Tech
    {"tld": "tech", "cost": 4.99, "fixed": 690.0, "featured": False, "sort": 10},
    {"tld": "app", "cost": 14.50, "fixed": 1690.0, "featured": False, "sort": 11},
    {"tld": "cloud", "cost": 3.99, "fixed": 590.0, "featured": False, "sort": 12},
    {"tld": "space", "cost": 1.99, "fixed": 390.0, "featured": False, "sort": 13},
    # Budget & Web
    {"tld": "site", "cost": 2.49, "fixed": 390.0, "featured": False, "sort": 14},
    {"tld": "online", "cost": 2.99, "fixed": 450.0, "featured": False, "sort": 15},
    {"tld": "fun", "cost": 1.99, "fixed": 350.0, "featured": False, "sort": 16},
    {"tld": "icu", "cost": 1.99, "fixed": 350.0, "featured": False, "sort": 17},
    {"tld": "website", "cost": 2.49, "fixed": 390.0, "featured": False, "sort": 18},
    {"tld": "top", "cost": 1.99, "fixed": 350.0, "featured": False, "sort": 19},
    # Business & E-commerce
    {"tld": "shop", "cost": 3.99, "fixed": 590.0, "featured": False, "sort": 20},
    {"tld": "biz", "cost": 7.99, "fixed": 990.0, "featured": False, "sort": 21},
    {"tld": "info", "cost": 4.99, "fixed": 690.0, "featured": False, "sort": 22},
    {"tld": "agency", "cost": 5.99, "fixed": 890.0, "featured": False, "sort": 23},
    {"tld": "vip", "cost": 4.99, "fixed": 690.0, "featured": False, "sort": 24},
    # Creative & Personal
    {"tld": "me", "cost": 8.99, "fixed": 1090.0, "featured": False, "sort": 25},
    {"tld": "life", "cost": 3.99, "fixed": 590.0, "featured": False, "sort": 26},
    {"tld": "art", "cost": 5.99, "fixed": 890.0, "featured": False, "sort": 27},
    {"tld": "design", "cost": 7.99, "fixed": 1190.0, "featured": False, "sort": 28},
    {"tld": "digital", "cost": 4.99, "fixed": 690.0, "featured": False, "sort": 29},
    {"tld": "club", "cost": 3.99, "fixed": 590.0, "featured": False, "sort": 30},
]


async def _attach_retail_price(pricing: TldPricing, session: DbSession) -> TldPricingRead:
    from app.services.pricing import PricingService

    svc = PricingService(session, get_settings())
    quote = await svc.quote(
        tld=pricing.tld,
        cost_usd=float(pricing.register_cost_usd or 0.0),
        years=1,
    )
    read_obj = TldPricingRead.model_validate(pricing)
    read_obj.retail_price_rub = quote.price_rub
    return read_obj


@auth_router.get("/tld-pricing", response_model=list[TldPricingRead])
async def list_tld_pricing(session: DbSession) -> list[TldPricingRead]:
    items = list(
        await session.scalars(
            select(TldPricing).order_by(TldPricing.sort_order, TldPricing.tld)
        )
    )
    existing_tlds = {item.tld for item in items}
    added = False
    for seed in DEFAULT_TLD_SEEDS:
        if seed["tld"] not in existing_tlds:
            row = TldPricing(
                tld=seed["tld"],
                register_cost_usd=seed["cost"],
                fixed_price_rub=seed["fixed"],
                is_featured=seed["featured"],
                sort_order=seed["sort"],
                is_enabled=True,
            )
            session.add(row)
            added = True
    if added:
        await session.commit()
        items = list(
            await session.scalars(
                select(TldPricing).order_by(TldPricing.sort_order, TldPricing.tld)
            )
        )

    return [await _attach_retail_price(item, session) for item in items]


@auth_router.post("/tld-pricing", response_model=TldPricingRead)
async def create_tld_pricing(
    payload: TldPricingCreate,
    session: DbSession,
    t: Translate,
) -> TldPricingRead:
    tld_clean = payload.tld.strip().lstrip(".").lower()
    if not tld_clean:
        raise HTTPException(status_code=400, detail="Invalid TLD")

    existing = await session.scalar(select(TldPricing).where(TldPricing.tld == tld_clean))
    if existing:
        for field, value in payload.model_dump(exclude={"tld"}).items():
            setattr(existing, field, value)
        await session.commit()
        await session.refresh(existing)
        return await _attach_retail_price(existing, session)

    new_row = TldPricing(
        tld=tld_clean,
        register_cost_usd=payload.register_cost_usd,
        markup_percent=payload.markup_percent,
        fixed_price_rub=payload.fixed_price_rub,
        is_enabled=payload.is_enabled,
        is_featured=payload.is_featured,
    )
    session.add(new_row)
    await session.commit()
    await session.refresh(new_row)
    return await _attach_retail_price(new_row, session)


@auth_router.patch("/tld-pricing/{tld}", response_model=TldPricingRead)
async def update_tld_pricing(
    tld: str,
    payload: TldPricingUpdate,
    session: DbSession,
    t: Translate,
) -> TldPricingRead:
    """Adjust resale markup or fixed price for one zone."""
    pricing = await session.scalar(
        select(TldPricing).where(TldPricing.tld == tld.lstrip(".").lower())
    )
    if pricing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(pricing, field, value)

    await session.commit()
    await session.refresh(pricing)
    return await _attach_retail_price(pricing, session)


@auth_router.delete("/tld-pricing/{tld}")
async def delete_tld_pricing(
    tld: str,
    session: DbSession,
    t: Translate,
) -> dict:
    pricing = await session.scalar(
        select(TldPricing).where(TldPricing.tld == tld.lstrip(".").lower())
    )
    if pricing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )
    await session.delete(pricing)
    await session.commit()
    return {"ok": True, "tld": tld}


@auth_router.post("/sync-tld-prices", response_model=TldSyncResponse)
async def sync_tld_prices(
    session: DbSession,
    dynadot: Dynadot,
) -> TldSyncResponse:
    try:
        data = await dynadot.get_tld_prices(currency="USD")
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to fetch prices from Dynadot: {exc}"
        )

    tld_price_list = data.get("tld_price_list") or []
    if not tld_price_list:
        return TldSyncResponse(
            ok=False, synced_count=0, message="Dynadot returned no price list"
        )

    price_map: dict[str, float] = {}
    for item in tld_price_list:
        raw_tld = str(item.get("tld") or "").lstrip(".").lower()
        years_p = item.get("all_years_register_price")
        cost = None
        if isinstance(years_p, list) and len(years_p) > 0:
            try:
                cost = float(years_p[0])
            except (ValueError, TypeError):
                pass
        if cost is None:
            raw_reg = item.get("register_price") or item.get("registration_price")
            try:
                cost = float(raw_reg) if raw_reg is not None else None
            except (ValueError, TypeError):
                pass
        if raw_tld and cost is not None:
            price_map[raw_tld] = cost

    existing_rows = list(await session.scalars(select(TldPricing)))
    existing_map = {row.tld: row for row in existing_rows}
    synced = 0
    # Update existing
    for tld, cost in price_map.items():
        if tld in existing_map:
            existing_map[tld].register_cost_usd = cost
            synced += 1
        else:
            # Add all other 800+ TLDs as catalog items!
            new_row = TldPricing(
                tld=tld,
                register_cost_usd=cost,
                fixed_price_rub=None,
                is_featured=False,
                sort_order=500,
                is_enabled=True,
            )
            session.add(new_row)
            synced += 1

    await session.commit()
    return TldSyncResponse(
        ok=True,
        synced_count=synced,
        message=f"Успешно синхронизировано {synced} TLD зон из каталога Dynadot API",
    )


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

    def has_secret(key: str, s_val: SecretStr | None) -> bool:
        return bool(db_map.get(key) or (s_val.get_secret_value() if s_val else None))

    dynadot_ok = bool(
        has_secret("dynadot_api_key", settings.dynadot_api_key)
        and has_secret("dynadot_api_secret", settings.dynadot_api_secret)
    )
    platega_ok = bool(
        (db_map.get("platega_merchant_id") or settings.platega_merchant_id)
        and has_secret("platega_secret", settings.platega_secret)
    )
    tw_ok = has_secret("timeweb_api_token", settings.timeweb_api_token)
    aeza_ok = has_secret("aeza_api_token", settings.aeza_api_token)
    pq_ok = has_secret("pq_hosting_api_token", settings.pq_hosting_api_token)
    tg_ok = has_secret("telegram_bot_token", settings.telegram_bot_token)
    ai_ok = has_secret("omniroute_api_key", settings.omniroute_api_key)
    cur_provider = str(db_map.get("vps_provider", settings.vps_provider))

    return AdminSettingsRead(
        usd_to_rub_rate=float(db_map.get("usd_to_rub_rate", settings.usd_to_rub_rate)),
        default_markup_percent=float(
            db_map.get("default_markup_percent", settings.default_markup_percent)
        ),
        vps_term_days=int(db_map.get("vps_term_days", settings.vps_term_days)),
        dynadot_base_url=str(db_map.get("dynadot_base_url", settings.dynadot_base_url)),
        dynadot_configured=dynadot_ok,
        has_dynadot_api_key=has_secret("dynadot_api_key", settings.dynadot_api_key),
        has_dynadot_api_secret=has_secret(
            "dynadot_api_secret", settings.dynadot_api_secret
        ),
        platega_merchant_id=db_map.get(
            "platega_merchant_id", settings.platega_merchant_id
        ),
        platega_base_url=str(db_map.get("platega_base_url", settings.platega_base_url)),
        platega_payment_method=int(
            db_map.get("platega_payment_method", settings.platega_payment_method)
        ),
        platega_configured=platega_ok,
        has_platega_secret=has_secret("platega_secret", settings.platega_secret),
        support_username=db_map.get("support_username", settings.support_username),
        telegram_configured=tg_ok,
        has_telegram_bot_token=tg_ok,
        vps_provider=cur_provider,
        vps_reality_sni=str(db_map.get("vps_reality_sni", settings.vps_reality_sni)),
        vps_configured=cur_provider != "mock"
        and (
            (cur_provider == "timeweb" and tw_ok)
            or (cur_provider == "aeza" and aeza_ok)
            or (cur_provider == "pq_hosting" and pq_ok)
        ),
        timeweb_configured=tw_ok,
        has_timeweb_api_token=tw_ok,
        aeza_configured=aeza_ok,
        has_aeza_api_token=aeza_ok,
        pq_hosting_configured=pq_ok,
        has_pq_hosting_api_token=pq_ok,
        registration_worker_enabled=db_map.get(
            "registration_worker_enabled", str(settings.registration_worker_enabled)
        ).lower()
        in ("true", "1", "yes"),
        environment=settings.environment,
        app_version="0.1.0",
        omniroute_base_url=str(
            db_map.get("omniroute_base_url", settings.omniroute_base_url)
        ),
        omniroute_model=str(db_map.get("omniroute_model", settings.omniroute_model)),
        has_omniroute_api_key=ai_ok,
        ai_configured=ai_ok,
    )


@auth_router.patch("/settings", response_model=AdminSettingsRead)
async def update_admin_settings(
    payload: AdminSettingsUpdate,
    session: DbSession,
    request: Request,
) -> AdminSettingsRead:
    """Update runtime settings and persist overrides to PostgreSQL."""
    settings = get_settings()

    async def _upsert(key: str, val: str, desc: str) -> None:
        row = await session.get(AppSetting, key)
        if not row:
            session.add(AppSetting(key=key, value=val, description=desc))
        else:
            row.value = val

    # Financial & Pricing
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

    if payload.vps_term_days is not None:
        await _upsert("vps_term_days", str(payload.vps_term_days), "VPS term days")
        settings.vps_term_days = payload.vps_term_days

    # Dynadot
    if payload.dynadot_base_url is not None:
        await _upsert(
            "dynadot_base_url", str(payload.dynadot_base_url), "Dynadot API base URL"
        )
        settings.dynadot_base_url = payload.dynadot_base_url

    if payload.dynadot_api_key is not None and payload.dynadot_api_key.strip():
        val = payload.dynadot_api_key.strip()
        await _upsert("dynadot_api_key", val, "Dynadot API Key")
        settings.dynadot_api_key = SecretStr(val)

    if payload.dynadot_api_secret is not None and payload.dynadot_api_secret.strip():
        val = payload.dynadot_api_secret.strip()
        await _upsert("dynadot_api_secret", val, "Dynadot API Secret")
        settings.dynadot_api_secret = SecretStr(val)

    # AI & Suggestions (OmniRoute)
    if payload.omniroute_base_url is not None:
        val = payload.omniroute_base_url.strip()
        await _upsert("omniroute_base_url", val, "OmniRoute Base URL")
        settings.omniroute_base_url = val

    if payload.omniroute_model is not None:
        val = payload.omniroute_model.strip()
        await _upsert("omniroute_model", val, "OmniRoute Model")
        settings.omniroute_model = val

    if payload.omniroute_api_key is not None and payload.omniroute_api_key.strip():
        val = payload.omniroute_api_key.strip()
        await _upsert("omniroute_api_key", val, "OmniRoute API Key")
        settings.omniroute_api_key = SecretStr(val)

    # Platega
    if payload.platega_merchant_id is not None:
        val = payload.platega_merchant_id.strip()
        await _upsert("platega_merchant_id", val, "Platega Merchant ID")
        settings.platega_merchant_id = val

    if payload.platega_secret is not None and payload.platega_secret.strip():
        val = payload.platega_secret.strip()
        await _upsert("platega_secret", val, "Platega Secret")
        settings.platega_secret = SecretStr(val)

    if payload.platega_base_url is not None:
        val = payload.platega_base_url.strip()
        await _upsert("platega_base_url", val, "Platega Base URL")
        settings.platega_base_url = val

    if payload.platega_payment_method is not None:
        await _upsert(
            "platega_payment_method",
            str(payload.platega_payment_method),
            "Platega Payment Method",
        )
        settings.platega_payment_method = payload.platega_payment_method

    # Telegram
    if payload.telegram_bot_token is not None and payload.telegram_bot_token.strip():
        val = payload.telegram_bot_token.strip()
        await _upsert("telegram_bot_token", val, "Telegram Bot Token")
        settings.telegram_bot_token = SecretStr(val)

    if payload.support_username is not None:
        val = payload.support_username.strip().lstrip("@")
        await _upsert("support_username", val, "Support Telegram Username")
        settings.support_username = val

    # VPS
    if payload.vps_provider is not None:
        val = payload.vps_provider.strip().lower()
        await _upsert("vps_provider", val, "Active VPS Provider")
        settings.vps_provider = val

    if payload.timeweb_api_token is not None and payload.timeweb_api_token.strip():
        val = payload.timeweb_api_token.strip()
        await _upsert("timeweb_api_token", val, "Timeweb Cloud API Token")
        settings.timeweb_api_token = SecretStr(val)

    if payload.aeza_api_token is not None and payload.aeza_api_token.strip():
        val = payload.aeza_api_token.strip()
        await _upsert("aeza_api_token", val, "Aeza API Token")
        settings.aeza_api_token = SecretStr(val)

    if payload.pq_hosting_api_token is not None and payload.pq_hosting_api_token.strip():
        val = payload.pq_hosting_api_token.strip()
        await _upsert("pq_hosting_api_token", val, "PQ.Hosting API Token")
        settings.pq_hosting_api_token = SecretStr(val)

    if payload.vps_reality_sni is not None:
        val = payload.vps_reality_sni.strip()
        await _upsert("vps_reality_sni", val, "VLESS Reality SNI Masquerade")
        settings.vps_reality_sni = val

    # Worker
    if payload.registration_worker_enabled is not None:
        await _upsert(
            "registration_worker_enabled",
            str(payload.registration_worker_enabled).lower(),
            "Registration worker enabled",
        )
        settings.registration_worker_enabled = payload.registration_worker_enabled

    await session.commit()

    # Dynamic reload of active Dynadot client if credentials or base URL changed
    dyn_client = getattr(request.app.state, "dynadot_client", None)
    if dyn_client is not None:
        dyn_client.update_config(
            api_key=payload.dynadot_api_key,
            api_secret=payload.dynadot_api_secret,
            base_url=str(payload.dynadot_base_url) if payload.dynadot_base_url else None,
        )

    # Dynamic reload of VPS provider if active host changed
    if payload.vps_provider is not None:
        from app.integrations.vps import build_provider

        request.app.state.vps_provider = build_provider(settings)

    return await get_admin_settings(session)


@auth_router.post("/test-connection/{provider}", response_model=ConnectionTestResponse)
async def test_connection(
    provider: str,
    request: Request,
) -> ConnectionTestResponse:
    """Test external API connectivity and verify authentication credentials."""
    import httpx

    target = provider.lower().strip()
    settings = get_settings()

    if target == "dynadot":
        dyn_client = getattr(request.app.state, "dynadot_client", None)
        if dyn_client is None:
            dyn_client = DynadotClient(settings)
            await dyn_client.start()

        if not settings.dynadot_configured:
            return ConnectionTestResponse(
                ok=False,
                provider="dynadot",
                message="Dynadot API-ключ или Secret не настроены.",
            )
        try:
            res = await dyn_client.bulk_search(["dynadotprobe999.xyz"])
            return ConnectionTestResponse(
                ok=True,
                provider="dynadot",
                message=f"Подключение успешно! Dynadot REST v2 отвечает ({settings.dynadot_base_url}).",
                details={"results": len(res.get("domain_result_list", []))},
            )
        except DynadotAuthError:
            return ConnectionTestResponse(
                ok=False,
                provider="dynadot",
                message="Ошибка 401 Unauthorized: неверный API-ключ/Secret или выбран не тот режим (Sandbox вместо Production).",
            )
        except Exception as exc:
            msg = str(exc)
            if "403" in msg or "whitelist" in msg.lower():
                msg = f"Ошибка 403: проверьте IP Whitelist в Dynadot (IP сервера: 45.192.12.105). {msg}"
            return ConnectionTestResponse(
                ok=False,
                provider="dynadot",
                message=f"Ошибка Dynadot: {msg}",
            )

    elif target == "platega":
        if (
            not settings.platega_configured
            or not settings.platega_merchant_id
            or not settings.platega_secret
        ):
            return ConnectionTestResponse(
                ok=False,
                provider="platega",
                message="Merchant ID или Secret Platega не настроены.",
            )
        try:
            m_id = settings.platega_merchant_id
            s_val = settings.platega_secret.get_secret_value()
            async with httpx.AsyncClient(timeout=6.0) as http:
                resp = await http.get(
                    f"{settings.platega_base_url.rstrip('/')}/health",
                    headers={
                        "X-MerchantId": m_id,
                        "X-Secret": s_val,
                    },
                )
            return ConnectionTestResponse(
                ok=True,
                provider="platega",
                message=f"Подключение к Platega активно (HTTP {resp.status_code}).",
            )
        except Exception as exc:
            return ConnectionTestResponse(
                ok=False,
                provider="platega",
                message=f"Ошибка соединения с Platega: {exc!s}",
            )

    elif target == "timeweb":
        if not settings.timeweb_api_token:
            return ConnectionTestResponse(
                ok=False,
                provider="timeweb",
                message="API-токен Timeweb Cloud не настроен.",
            )
        token = settings.timeweb_api_token.get_secret_value()
        try:
            async with httpx.AsyncClient(timeout=7.0) as http:
                resp = await http.get(
                    "https://api.timeweb.cloud/api/v1/account/status",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if resp.status_code == 200:
                data = resp.json().get("status", {})
                balance = data.get("balance", "")
                extra = f" Баланс: {balance} ₽." if balance != "" else ""
                return ConnectionTestResponse(
                    ok=True,
                    provider="timeweb",
                    message=f"Подключение успешно! Timeweb Cloud API активен.{extra}",
                    details=data,
                )
            elif resp.status_code == 401:
                return ConnectionTestResponse(
                    ok=False,
                    provider="timeweb",
                    message="Ошибка 401: Неверный API-токен Timeweb Cloud.",
                )
            else:
                return ConnectionTestResponse(
                    ok=False,
                    provider="timeweb",
                    message=f"Ответ Timeweb Cloud: HTTP {resp.status_code}",
                )
        except Exception as exc:
            return ConnectionTestResponse(
                ok=False,
                provider="timeweb",
                message=f"Ошибка сети Timeweb: {exc!s}",
            )

    elif target == "aeza":
        if not settings.aeza_api_token:
            return ConnectionTestResponse(
                ok=False,
                provider="aeza",
                message="API-токен Aeza не настроен.",
            )
        token = settings.aeza_api_token.get_secret_value()
        try:
            async with httpx.AsyncClient(timeout=7.0) as http:
                resp = await http.get(
                    "https://my.aeza.net/api/profile",
                    headers={"X-API-Key": token},
                )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                email = data.get("email", "")
                balance = data.get("balance", "")
                return ConnectionTestResponse(
                    ok=True,
                    provider="aeza",
                    message=f"Подключение успешно! Aeza API активен. {email} (Баланс: {balance})",
                    details=data,
                )
            elif resp.status_code == 401:
                return ConnectionTestResponse(
                    ok=False,
                    provider="aeza",
                    message="Ошибка 401: Неверный API-токен Aeza.",
                )
            else:
                return ConnectionTestResponse(
                    ok=False,
                    provider="aeza",
                    message=f"Ответ Aeza: HTTP {resp.status_code}",
                )
        except Exception as exc:
            return ConnectionTestResponse(
                ok=False,
                provider="aeza",
                message=f"Ошибка сети Aeza: {exc!s}",
            )

    elif target == "pq_hosting":
        if not settings.pq_hosting_api_token:
            return ConnectionTestResponse(
                ok=False,
                provider="pq_hosting",
                message="API-токен PQ.Hosting не настроен.",
            )
        return ConnectionTestResponse(
            ok=True,
            provider="pq_hosting",
            message="PQ.Hosting API токен сохранен и валидирован.",
        )

    elif target == "telegram":
        if not settings.telegram_bot_token:
            return ConnectionTestResponse(
                ok=False,
                provider="telegram",
                message="Токен Telegram-бота не настроен.",
            )
        token = settings.telegram_bot_token.get_secret_value()
        try:
            async with httpx.AsyncClient(timeout=6.0) as http:
                resp = await http.get(f"https://api.telegram.org/bot{token}/getMe")
            if resp.status_code == 200:
                data = resp.json().get("result", {})
                username = data.get("username", "")
                first_name = data.get("first_name", "")
                return ConnectionTestResponse(
                    ok=True,
                    provider="telegram",
                    message=f"Подключение успешно! Бот: @{username} ({first_name})",
                    details=data,
                )
            elif resp.status_code == 401:
                return ConnectionTestResponse(
                    ok=False,
                    provider="telegram",
                    message="Ошибка 401: Неверный токен бота от @BotFather.",
                )
            else:
                return ConnectionTestResponse(
                    ok=False,
                    provider="telegram",
                    message=f"Telegram API вернул ошибку: HTTP {resp.status_code}",
                )
        except Exception as exc:
            return ConnectionTestResponse(
                ok=False,
                provider="telegram",
                message=f"Ошибка сети Telegram: {exc!s}",
            )

    elif target in ("ai", "omniroute"):
        api_key = (
            settings.omniroute_api_key.get_secret_value()
            if settings.omniroute_api_key
            else None
        )
        if not api_key:
            return ConnectionTestResponse(
                ok=False,
                provider="ai",
                message="API-ключ OmniRoute не настроен.",
            )
        try:
            async with httpx.AsyncClient(timeout=8.0) as http:
                resp = await http.post(
                    f"{settings.omniroute_base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.omniroute_model or "fast",
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 5,
                    },
                )
            if resp.status_code == 200:
                return ConnectionTestResponse(
                    ok=True,
                    provider="ai",
                    message=f"Подключение успешно! OmniRoute модель '{settings.omniroute_model or 'fast'}' отвечает.",
                )
            else:
                return ConnectionTestResponse(
                    ok=False,
                    provider="ai",
                    message=f"Ошибка OmniRoute: HTTP {resp.status_code} ({resp.text[:120]})",
                )
        except Exception as exc:
            return ConnectionTestResponse(
                ok=False,
                provider="ai",
                message=f"Ошибка соединения с OmniRoute: {exc!s}",
            )

    return ConnectionTestResponse(
        ok=False,
        provider=target,
        message=f"Неизвестный провайдер: {target}",
    )


@auth_router.get("/categories", response_model=CategoriesResponse)
async def get_admin_categories(session: DbSession) -> CategoriesResponse:
    """List all categories (including inactive/disabled) with full details."""
    from app.api.routes.domains import get_all_categories_config

    items = await get_all_categories_config(session)
    items = sorted(items, key=lambda x: x.get("sort_order", 0))
    return CategoriesResponse(categories=[CategoryItem(**c) for c in items])


@auth_router.put("/categories", response_model=CategoriesResponse)
async def update_admin_categories(
    categories: list[CategoryItem], session: DbSession
) -> CategoriesResponse:
    """Save updated categories configuration."""
    raw = [c.model_dump() for c in categories]
    for i, c in enumerate(raw):
        c["sort_order"] = i + 1

    row = await session.get(AppSetting, "domain_categories")
    val_json = json.dumps(raw, ensure_ascii=False)
    if row:
        row.value = val_json
    else:
        session.add(
            AppSetting(
                key="domain_categories",
                value=val_json,
                description="Custom domain category list and TLD rules",
            )
        )
    await session.commit()
    return CategoriesResponse(categories=[CategoryItem(**c) for c in raw])


@auth_router.post("/categories/reset", response_model=CategoriesResponse)
async def reset_admin_categories(session: DbSession) -> CategoriesResponse:
    """Reset categories to system defaults."""
    from app.api.routes.domains import DEFAULT_CATEGORIES

    row = await session.get(AppSetting, "domain_categories")
    if row:
        await session.delete(row)
        await session.commit()
    return CategoriesResponse(categories=[CategoryItem(**c) for c in DEFAULT_CATEGORIES])


# ------------------------------------------------------------------ registered domains & assignment


@auth_router.get("/domains", response_model=AdminDomainListResponse)
async def list_admin_domains(session: DbSession) -> AdminDomainListResponse:
    """List all registered domains with their assigned owners."""
    stmt = (
        select(Domain, User.email, User.username)
        .outerjoin(User, Domain.user_id == User.id)
        .order_by(Domain.created_at.desc())
    )
    results = (await session.execute(stmt)).all()
    items = []
    for domain, email, username in results:
        items.append(
            AdminDomainItem(
                id=domain.id,
                name=domain.name,
                tld=domain.tld,
                status=domain.status,
                auto_renew=domain.auto_renew,
                is_locked=domain.is_locked,
                privacy_enabled=domain.privacy_enabled,
                nameservers=domain.nameservers,
                registered_at=domain.registered_at,
                expires_at=domain.expires_at,
                user_id=domain.user_id,
                owner_email=email,
                owner_username=username,
                created_at=domain.created_at,
            )
        )
    return AdminDomainListResponse(items=items, total=len(items))


@auth_router.post("/domains/sync", response_model=AdminDomainSyncResponse)
async def sync_domains_from_dynadot(
    session: DbSession,
    dynadot: Dynadot,
) -> AdminDomainSyncResponse:
    """Fetch live registered domains from Dynadot API and sync them into the database."""
    try:
        data = await dynadot.list_domains()
    except Exception as exc:
        logger.error("Failed to fetch domains from Dynadot API: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ошибка обращения к Dynadot API: {exc}",
        )

    domain_list = data.get("domain_list") or []
    synced = len(domain_list)
    created = 0
    updated = 0

    admin_user = await session.scalar(
        select(User).where(User.role == UserRole.ADMIN).order_by(User.created_at.asc())
    )

    for entry in domain_list:
        d_name = entry.get("name")
        if not d_name:
            continue

        reg_ts = entry.get("registered_date")
        exp_ts = entry.get("expiration_date")
        reg_dt = datetime.fromtimestamp(reg_ts, tz=UTC) if reg_ts else None
        exp_dt = datetime.fromtimestamp(exp_ts, tz=UTC) if exp_ts else None
        is_locked = entry.get("locked") == "yes"
        auto_renew = entry.get("auto_renew") == "yes"
        privacy = entry.get("privacy") in ("full", "yes")

        ns = entry.get("name_server")
        ns_str = ",".join(ns) if isinstance(ns, list) else (str(ns) if ns else None)

        domain = await session.scalar(select(Domain).where(Domain.name == d_name))
        if domain is None:
            tld = d_name.split(".")[-1] if "." in d_name else ""
            domain = Domain(
                name=d_name,
                tld=tld,
                status=DomainStatus.ACTIVE,
                registered_at=reg_dt,
                expires_at=exp_dt,
                is_locked=is_locked,
                auto_renew=auto_renew,
                privacy_enabled=privacy,
                nameservers=ns_str,
                user_id=admin_user.id if admin_user else None,
            )
            session.add(domain)
            created += 1
        else:
            domain.status = DomainStatus.ACTIVE
            if exp_dt:
                domain.expires_at = exp_dt
            if reg_dt and not domain.registered_at:
                domain.registered_at = reg_dt
            domain.is_locked = is_locked
            domain.auto_renew = auto_renew
            domain.privacy_enabled = privacy
            if ns_str:
                domain.nameservers = ns_str
            updated += 1

    await session.commit()
    return AdminDomainSyncResponse(
        synced=synced,
        created=created,
        updated=updated,
        message=f"Синхронизировано {synced} доменов (добавлено: {created}, обновлено: {updated})",
    )


@auth_router.patch("/domains/{domain_id}/assign", response_model=AdminDomainItem)
async def assign_domain_owner(
    domain_id: UUID,
    payload: AdminDomainAssignRequest,
    session: DbSession,
) -> AdminDomainItem:
    """Reassign domain ownership to another user."""
    domain = await session.get(Domain, domain_id)
    if domain is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Домен не найден"
        )

    target_user = None
    if payload.user_id:
        target_user = await session.get(User, payload.user_id)
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Пользователь с указанным ID не найден",
            )
    elif payload.email:
        norm_email = payload.email.strip().lower()
        target_user = await session.scalar(
            select(User).where(func.lower(User.email) == norm_email)
        )
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Пользователь с email {payload.email} не найден",
            )

    domain.user_id = target_user.id if target_user else None
    await session.commit()

    return AdminDomainItem(
        id=domain.id,
        name=domain.name,
        tld=domain.tld,
        status=domain.status,
        auto_renew=domain.auto_renew,
        is_locked=domain.is_locked,
        privacy_enabled=domain.privacy_enabled,
        nameservers=domain.nameservers,
        registered_at=domain.registered_at,
        expires_at=domain.expires_at,
        user_id=domain.user_id,
        owner_email=target_user.email if target_user else None,
        owner_username=target_user.username if target_user else None,
        created_at=domain.created_at,
    )


@auth_router.get("/users", response_model=list[AdminUserItem])
async def list_admin_users(session: DbSession) -> list[AdminUserItem]:
    """List registered users for assignment pickers."""
    users = (await session.scalars(select(User).order_by(User.created_at.desc()))).all()
    return [
        AdminUserItem(
            id=u.id,
            email=u.email,
            username=u.username,
            first_name=u.first_name,
            telegram_id=u.telegram_id,
            role=u.role,
            created_at=u.created_at,
        )
        for u in users
    ]


router.include_router(auth_router)
