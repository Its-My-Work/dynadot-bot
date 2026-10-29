"""FastAPI application factory and lifespan wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine, get_engine
from app.i18n import parse_accept_language, translate
from app.integrations.dynadot import DynadotClient
from app.integrations.dynadot.exceptions import (
    DomainUnavailableError,
    DynadotError,
    DynadotRateLimitError,
)
from app.integrations.payments import PlategaGateway
from app.integrations.payments.exceptions import (
    PaymentConfigError,
    PaymentError,
    PaymentTransportError,
)

logger = get_logger(__name__)


async def _start_bot(app: FastAPI) -> None:
    """Attach a bot + dispatcher so the Telegram webhook route can serve.

    Only webhook mode is started in-process; polling runs as its own service
    (`python -m app.bot`) because two pollers on one token fight each other.
    """
    if not settings.telegram_configured:
        logger.info("telegram bot disabled (no token)")
        return

    webhook_url = settings.telegram_webhook_url
    if not webhook_url:
        logger.info("telegram webhook base url unset; run the bot via polling")
        return

    from app.bot.runner import create_bot, create_dispatcher, set_bot_commands

    bot = create_bot(settings)
    dispatcher = create_dispatcher(settings)
    app.state.bot = bot
    app.state.bot_dispatcher = dispatcher

    await set_bot_commands(bot, settings)
    secret = settings.telegram_webhook_secret
    await bot.set_webhook(
        webhook_url,
        secret_token=secret.get_secret_value() if secret else None,
        drop_pending_updates=True,
    )
    logger.info("telegram webhook registered", extra={"url": webhook_url})


async def _stop_bot(app: FastAPI) -> None:
    bot = getattr(app.state, "bot", None)
    if bot is not None:
        await bot.session.close()
        app.state.bot = None
        app.state.bot_dispatcher = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start and stop shared resources exactly once per process."""
    configure_logging()
    logger.info(
        "starting application",
        extra={"environment": settings.environment, "app": settings.app_name},
    )

    get_engine()  # fail fast on a malformed DSN

    # One client — and therefore one serialising queue — per process.
    client = DynadotClient(settings)
    await client.start()
    app.state.dynadot_client = client

    # One payment gateway per process so the HTTP pool is shared.
    gateway = PlategaGateway(settings)
    await gateway.start()
    app.state.payment_gateway = gateway
    if not settings.platega_configured:
        # Not fatal: search works without payments, and failing startup would
        # take the whole API down over an unconfigured optional integration.
        logger.warning("platega credentials missing; checkout will fail")

    app.state.bot = None
    app.state.bot_dispatcher = None
    await _start_bot(app)

    # Optional in-process worker. Off by default so a multi-replica API does
    # not run N workers against Dynadot's single request slot.
    app.state.registration_worker = None
    if settings.registration_worker_enabled:
        from app.db.session import get_session_factory
        from app.services.worker import RegistrationWorker

        worker = RegistrationWorker(
            get_session_factory(),
            client,
            settings=settings,
            bot=app.state.bot,
            poll_interval=settings.registration_worker_interval,
        )
        await worker.start()
        app.state.registration_worker = worker

    try:
        yield
    finally:
        if app.state.registration_worker is not None:
            await app.state.registration_worker.stop()
        await _stop_bot(app)
        await gateway.aclose()
        await client.aclose()
        await dispose_engine()
        logger.info("application stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Domain registration service via Dynadot (Telegram Bot + TMA + Web)",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url=f"{settings.api_v1_prefix}/docs",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        # The TMA runs inside Telegram's webview on an arbitrary origin.
        allow_origins=["*"] if not settings.is_production else [settings.tma_url or ""],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    _register_exception_handlers(app)
    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built TMA / admin bundle when it exists.

    Mounted last so it can own ``/`` without shadowing the API routes. A
    missing ``dist`` is normal (API-only deploys, tests, a separately hosted
    CDN) and must not fail startup.
    """
    from pathlib import Path

    dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if not dist.is_dir():
        logger.info("frontend bundle not found; serving API only")
        return

    from fastapi.staticfiles import StaticFiles

    # html=True makes the mount serve index.html for unknown paths, which is
    # what a client-side app needs.
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
    logger.info("frontend mounted", extra={"path": str(dist)})


def _register_exception_handlers(app: FastAPI) -> None:
    """Translate integration errors into localised HTTP responses."""

    def _lang(request: Request) -> str:
        return parse_accept_language(request.headers.get("Accept-Language"))

    @app.exception_handler(DynadotRateLimitError)
    async def _rate_limited(request: Request, exc: DynadotRateLimitError):
        return JSONResponse(
            status_code=429,
            content={"detail": translate("error.rate_limited", _lang(request))},
        )

    @app.exception_handler(DomainUnavailableError)
    async def _unavailable(request: Request, exc: DomainUnavailableError):
        return JSONResponse(
            status_code=409,
            content={"detail": translate("search.taken", _lang(request))},
        )

    @app.exception_handler(DynadotError)
    async def _dynadot_error(request: Request, exc: DynadotError):
        logger.error("unhandled dynadot error", extra={"error": str(exc)})
        return JSONResponse(
            status_code=503,
            content={
                "detail": translate("error.registrar_unavailable", _lang(request))
            },
        )

    @app.exception_handler(PaymentConfigError)
    async def _payment_unconfigured(request: Request, exc: PaymentConfigError):
        logger.error("payment gateway unconfigured", extra={"error": str(exc)})
        return JSONResponse(
            status_code=503,
            content={"detail": translate("payment.unavailable", _lang(request))},
        )

    @app.exception_handler(PaymentTransportError)
    async def _payment_transport(request: Request, exc: PaymentTransportError):
        # 503 so the caller knows a retry is worthwhile.
        logger.warning("payment gateway unreachable", extra={"error": str(exc)})
        return JSONResponse(
            status_code=503,
            content={"detail": translate("payment.unavailable", _lang(request))},
        )

    @app.exception_handler(PaymentError)
    async def _payment_error(request: Request, exc: PaymentError):
        logger.error("unhandled payment error", extra={"error": str(exc)})
        return JSONResponse(
            status_code=502,
            content={"detail": translate("payment.failed", _lang(request))},
        )


app = create_app()
