"""Scheduled tasks: Daily & Weekly Digests for spaced repetition.

Deployment note: when the app runs on multiple Fly.io machines, all of them
boot APScheduler. To avoid every user receiving N copies of the same digest,
every job acquires a Postgres advisory lock (``pg_try_advisory_lock``) before
doing work. Only the machine that wins the lock for a given (job, date) will
actually send messages; the other machines no-op quietly.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func as sa_func, select, text

from bot.config import settings
from bot.db.engine import async_session
from bot.db.models import Document, User
from bot.services.formatting import tg_escape

logger = logging.getLogger(__name__)


def _lock_key(job_name: str) -> int:
    """Stable 63-bit integer lock key per (job, UTC date)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw = f"cortex:{job_name}:{today}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") & ((1 << 63) - 1)


async def _try_acquire_daily_lock(session, job_name: str) -> bool:
    """Try to take a Postgres advisory lock for today's run of ``job_name``.

    Returns True if we own it (and MUST release via _release_lock). The lock
    is released automatically when the session/connection ends, so even if
    this machine crashes mid-run the next cron tick can retry.
    """
    key = _lock_key(job_name)
    result = await session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
    got = bool(result.scalar())
    if not got:
        logger.info("Scheduler lock busy for %s — another machine is handling it", job_name)
    return got


async def _release_lock(session, job_name: str) -> None:
    await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _lock_key(job_name)})

TYPE_EMOJI = {
    "url": "🔗", "youtube": "📺", "pdf": "📄", "voice": "🎙", "text": "📝",
}


async def send_daily_digest(bot: Bot) -> None:
    """Send one random old card to each user (spaced repetition)."""
    async with async_session() as session:
        if not await _try_acquire_daily_lock(session, "daily_digest"):
            return
        try:
            users_result = await session.execute(select(User))
            users = users_result.scalars().all()
        except Exception:
            await _release_lock(session, "daily_digest")
            raise

        for user in users:
            try:
                result = await session.execute(
                    select(Document)
                    .where(Document.user_id == user.id)
                    .where(
                        Document.created_at <= text("NOW() - INTERVAL '7 days'")
                    )
                    .order_by(text("RANDOM()"))
                    .limit(1)
                )
                doc = result.scalar_one_or_none()
                if doc is None:
                    continue

                tags = ""
                if doc.tags:
                    try:
                        tags = " ".join(json.loads(doc.tags))
                    except (json.JSONDecodeError, TypeError):
                        pass

                emoji = TYPE_EMOJI.get(doc.source_type, "📄")
                msg = (
                    f"📬 <b>Daily Digest — вспомни!</b>\n\n"
                    f"{emoji} <b>{tg_escape(doc.title or 'Без названия')}</b>\n"
                )
                if doc.source_url:
                    msg += f"🔗 {tg_escape(doc.source_url)}\n"
                if doc.summary:
                    msg += f"\n{tg_escape(doc.summary)}\n"
                if tags:
                    msg += f"\n🏷 {tg_escape(tags)}"

                await bot.send_message(
                    chat_id=user.telegram_id, text=msg, parse_mode="HTML"
                )
            except Exception as e:
                logger.error(
                    "Failed to send digest to user %s: %s", user.telegram_id, e
                )
        # Session close naturally drops the advisory lock; release explicitly too.
        await _release_lock(session, "daily_digest")


async def send_weekly_digest(bot: Bot) -> None:
    """Weekly summary: what you saved this week, top tags, total growth."""
    async with async_session() as session:
        if not await _try_acquire_daily_lock(session, "weekly_digest"):
            return
        users_result = await session.execute(select(User))
        users = users_result.scalars().all()

        for user in users:
            try:
                # Count docs saved this week
                week_result = await session.execute(
                    select(sa_func.count(Document.id))
                    .where(Document.user_id == user.id)
                    .where(Document.created_at >= text("NOW() - INTERVAL '7 days'"))
                )
                week_count = week_result.scalar() or 0

                if week_count == 0:
                    continue

                # Total docs
                total_result = await session.execute(
                    select(sa_func.count(Document.id))
                    .where(Document.user_id == user.id)
                )
                total = total_result.scalar() or 0

                # This week's docs by type
                type_result = await session.execute(
                    select(Document.source_type, sa_func.count(Document.id))
                    .where(Document.user_id == user.id)
                    .where(Document.created_at >= text("NOW() - INTERVAL '7 days'"))
                    .group_by(Document.source_type)
                )
                type_lines = []
                for stype, cnt in type_result:
                    emoji = TYPE_EMOJI.get(stype, "📄")
                    type_lines.append(f"  {emoji} {stype}: {cnt}")

                msg = (
                    f"📊 <b>Итоги недели</b>\n\n"
                    f"📥 Сохранено за неделю: <b>{week_count}</b>\n"
                )
                if type_lines:
                    msg += "\n".join(type_lines) + "\n"
                msg += (
                    f"\n📚 Всего в базе: <b>{total}</b>\n\n"
                    f"💪 Продолжай в том же духе!"
                )

                await bot.send_message(
                    chat_id=user.telegram_id, text=msg, parse_mode="HTML"
                )
            except Exception as e:
                logger.error(
                    "Failed to send weekly digest to user %s: %s",
                    user.telegram_id, e,
                )
        await _release_lock(session, "weekly_digest")


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    # Daily digest at configured hour
    scheduler.add_job(
        send_daily_digest,
        "cron",
        hour=settings.daily_digest_hour,
        minute=0,
        args=[bot],
    )
    # Weekly digest every Sunday at 10:00
    scheduler.add_job(
        send_weekly_digest,
        "cron",
        day_of_week="sun",
        hour=10,
        minute=0,
        args=[bot],
    )
    return scheduler
