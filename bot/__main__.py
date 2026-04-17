"""Entry point: python -m bot"""

import asyncio
import logging
import logging.handlers
import os

from aiohttp import web
from aiogram import Bot, Dispatcher

from bot.config import settings
from bot.handlers import main_router
from bot.middlewares import (
    AntiSpamMiddleware,
    AuditLogMiddleware,
    FileSizeMiddleware,
    InputSanitizeMiddleware,
    PrivateOnlyMiddleware,
    RateLimitMiddleware,
    WhitelistMiddleware,
)
from bot.scheduler import setup_scheduler
from bot.webapp_api import create_webapp_app

# ── Logging setup ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Security audit log → separate file (skip if filesystem is read-only, e.g. Fly.io)
audit_logger = logging.getLogger("audit")
audit_logger.setLevel(logging.INFO)
try:
    _audit_handler = logging.handlers.RotatingFileHandler(
        "audit.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    _audit_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(message)s")
    )
    audit_logger.addHandler(_audit_handler)
except OSError:
    audit_logger.addHandler(logging.StreamHandler())


async def main() -> None:
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    # ── Security middleware stack (order matters!) ──
    # Layer 1: block groups/channels
    dp.message.middleware(PrivateOnlyMiddleware())
    # Layer 2: whitelist (if ALLOWED_USERS is set)
    dp.message.middleware(WhitelistMiddleware())
    dp.callback_query.middleware(WhitelistMiddleware())
    # Layer 3: anti-spam (repeated identical messages)
    dp.message.middleware(AntiSpamMiddleware(max_repeats=3, window_seconds=30))
    # Layer 4: rate-limit (messages + callbacks)
    rate_limiter = RateLimitMiddleware(max_events=15, window_seconds=60)
    dp.message.middleware(rate_limiter)
    dp.callback_query.middleware(rate_limiter)
    # Layer 5: file size guard
    dp.message.middleware(FileSizeMiddleware())
    # Layer 6: input sanitization (text length)
    dp.message.middleware(InputSanitizeMiddleware())
    # Layer 7: audit log
    dp.message.middleware(AuditLogMiddleware())
    dp.callback_query.middleware(AuditLogMiddleware())

    dp.include_router(main_router)

    # Scheduler
    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("Scheduler started (Daily Digest at %02d:00)", settings.daily_digest_hour)

    # Web API for Mini App — bind to 127.0.0.1 (not exposed to internet)
    webapp = create_webapp_app()
    runner = web.AppRunner(webapp)
    await runner.setup()
    bind_host = "127.0.0.1"
    site = web.TCPSite(runner, bind_host, 8080)
    await site.start()
    logger.info("Web API started on http://%s:8080", bind_host)

    # Security summary on startup
    wl = settings.allowed_user_ids
    logger.info(
        "🔒 Security: private_only=%s whitelist=%s admins=%s max_text=%d",
        settings.private_only,
        f"{len(wl)} users" if wl else "OFF",
        len(settings.admin_user_ids) or "none",
        settings.max_text_length,
    )

    # Start polling
    logger.info("Bot is starting...")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
