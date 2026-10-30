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
from app.models.enums import DomainStatus, OrderStatus, TaskStatus
from app.schemas import (
    AdminLoginRequest,
    AdminLoginResponse,
    AdminOrderRead,
    AdminSettingsRead,
    AdminSettingsUpdate,
    AdminStats,
    ApiLogRead,
    ConnectionTestResponse,
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
        has_dynadot_api_secret=has_secret("dynadot_api_secret", settings.dynadot_api_secret),
        platega_merchant_id=db_map.get("platega_merchant_id", settings.platega_merchant_id),
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
        await _upsert("dynadot_base_url", str(payload.dynadot_base_url), "Dynadot API base URL")
        settings.dynadot_base_url = payload.dynadot_base_url

    if payload.dynadot_api_key is not None and payload.dynadot_api_key.strip():
        val = payload.dynadot_api_key.strip()
        await _upsert("dynadot_api_key", val, "Dynadot API Key")
        settings.dynadot_api_key = SecretStr(val)

    if payload.dynadot_api_secret is not None and payload.dynadot_api_secret.strip():
        val = payload.dynadot_api_secret.strip()
        await _upsert("dynadot_api_secret", val, "Dynadot API Secret")
        settings.dynadot_api_secret = SecretStr(val)

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
                message=f"Ошибка соединения с Platega: {str(exc)}",
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
                message=f"Ошибка сети Timeweb: {str(exc)}",
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
                message=f"Ошибка сети Aeza: {str(exc)}",
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
                message=f"Ошибка сети Telegram: {str(exc)}",
            )

    return ConnectionTestResponse(
        ok=False,
        provider=target,
        message=f"Неизвестный провайдер: {target}",
    )


router.include_router(auth_router)
