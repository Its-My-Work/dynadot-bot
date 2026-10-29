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

logger = get_logger(__name__)


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

    try:
        yield
    finally:
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
    return app


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


app = create_app()
