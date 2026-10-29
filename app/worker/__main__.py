"""Standalone entrypoint for the registration worker.

Run as its own process (`python -m app.worker`) in production. It is separate
from the API because the two scale differently and because a single worker
process is exactly what Dynadot's one-request-at-a-time limit wants — two API
replicas would otherwise run two workers and contend for the same tasks.

The in-API worker (enabled with ``REGISTRATION_WORKER_ENABLED=true``) exists
for single-container and local setups.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine, get_session_factory
from app.integrations.dynadot import DynadotClient
from app.services.worker import RegistrationWorker

logger = get_logger(__name__)


async def _create_bot_or_none(settings):
    """Build a Bot only for notifications; a missing token is not fatal."""
    if not settings.telegram_configured:
        logger.info("telegram token missing; worker will not notify users")
        return None
    from app.bot.runner import create_bot

    return create_bot(settings)


async def main() -> None:
    configure_logging()
    settings = get_settings()

    client = DynadotClient(settings)
    await client.start()
    bot = await _create_bot_or_none(settings)

    worker = RegistrationWorker(
        get_session_factory(),
        client,
        settings=settings,
        bot=bot,
        poll_interval=settings.registration_worker_interval,
    )
    await worker.start()
    logger.info("registration worker process ready")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    try:
        await stop.wait()
    finally:
        await worker.stop()
        if bot is not None:
            await bot.session.close()
        await client.aclose()
        await dispose_engine()
        logger.info("registration worker process stopped")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
