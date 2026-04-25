"""Entry point: python -m bot"""

import asyncio
import logging
import logging.handlers

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

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


def _build_dispatcher(bot: Bot) -> Dispatcher:
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
    return dp


async def _health(_: web.Request) -> web.Response:
    return web.Response(text="ok")


def _build_public_app() -> web.Application:
    """Public aiohttp app: /health + Mini App API under /api."""
    app = web.Application()
    app.router.add_get("/health", _health)
    # Mount the WebApp API as a sub-app at /api
    app.add_subapp("/api", create_webapp_app())
    return app


async def _run_polling(bot: Bot, dp: Dispatcher) -> None:
    """Polling mode — good for local dev; also runs the health/API server.

    Binds to 0.0.0.0 so Fly.io's HTTP health check on /health can reach us.
    The WebApp API itself is already protected by Telegram initData HMAC
    validation, so exposing it is safe.
    """
    app = _build_public_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.web_port)
    await site.start()
    logger.info("HTTP (polling mode) on 0.0.0.0:%d", settings.web_port)

    # Clear any stale webhook before polling so we don't get 409 Conflict
    await bot.delete_webhook(drop_pending_updates=False)
    logger.info("Starting long polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


async def _run_webhook(bot: Bot, dp: Dispatcher) -> None:
    """Webhook mode — Telegram POSTs updates to us via HTTPS."""
    public_url = settings.webhook_public_url.rstrip("/")
    if not public_url:
        raise RuntimeError(
            "USE_WEBHOOK=1 requires WEBHOOK_PUBLIC_URL "
            "(e.g. https://stwdz-second-brain.fly.dev)"
        )

    full_webhook_url = public_url + settings.webhook_path
    await bot.set_webhook(
        url=full_webhook_url,
        secret_token=settings.webhook_secret or None,
        drop_pending_updates=False,
        allowed_updates=dp.resolve_used_update_types(),
    )
    logger.info("Webhook registered: %s", full_webhook_url)

    app = _build_public_app()
    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret or None,
    ).register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.web_port)
    await site.start()
    logger.info("Webhook server on 0.0.0.0:%d%s", settings.web_port, settings.webhook_path)

    # Run forever
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


async def _set_bot_commands(bot: Bot) -> None:
    """Register the slash-menu shown by Telegram when the user types '/'.

    Keep the list short — menu buttons already cover the daily actions.
    These are the advanced/rare commands worth discovering via the "/" UI.
    """
    commands = [
        BotCommand(command="start", description="Початок + головне меню"),
        BotCommand(command="menu", description="Показати меню знову"),
        BotCommand(command="help", description="Повна довідка"),
        BotCommand(command="ask", description="Спитати по своїй базі знань"),
        BotCommand(command="search", description="Швидкий текстовий пошук"),
        BotCommand(command="random", description="Випадкова нотатка"),
        BotCommand(command="quiz", description="Квіз по твоїх матеріалах"),
        BotCommand(command="pinned", description="Закріплені нотатки"),
        BotCommand(command="stats", description="Статистика"),
        BotCommand(command="conspect", description="Конспект із тексту"),
        BotCommand(command="tts", description="Озвучити текст"),
        BotCommand(command="export", description="Експорт у Markdown"),
    ]
    try:
        await bot.set_my_commands(commands)
    except Exception as e:
        logger.warning("Failed to register bot commands: %s", e)


async def main() -> None:
    bot = Bot(token=settings.bot_token)
    dp = _build_dispatcher(bot)

    # Register the Telegram "/" menu so users discover commands visually.
    await _set_bot_commands(bot)

    # Scheduler
    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("Scheduler started (Daily Digest at %02d:00)", settings.daily_digest_hour)

    # Security summary on startup
    wl = settings.allowed_user_ids
    logger.info(
        "🔒 Security: private_only=%s whitelist=%s admins=%s max_text=%d",
        settings.private_only,
        f"{len(wl)} users" if wl else "OFF",
        len(settings.admin_user_ids) or "none",
        settings.max_text_length,
    )

    mode = "webhook" if settings.use_webhook else "polling"
    logger.info("Bot starting in %s mode...", mode)

    try:
        if settings.use_webhook:
            await _run_webhook(bot, dp)
        else:
            await _run_polling(bot, dp)
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
