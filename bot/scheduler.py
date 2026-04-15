"""Scheduled tasks: Daily & Weekly Digests for spaced repetition."""

import json
import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func as sa_func, select, text

from bot.config import settings
from bot.db.engine import async_session
from bot.db.models import Document, User

logger = logging.getLogger(__name__)

TYPE_EMOJI = {
    "url": "🔗", "youtube": "📺", "pdf": "📄", "voice": "🎙", "text": "📝",
}


async def send_daily_digest(bot: Bot) -> None:
    """Send one random old card to each user (spaced repetition)."""
    async with async_session() as session:
        users_result = await session.execute(select(User))
        users = users_result.scalars().all()

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
                    f"{emoji} <b>{doc.title or 'Без названия'}</b>\n"
                )
                if doc.source_url:
                    msg += f"🔗 {doc.source_url}\n"
                if doc.summary:
                    msg += f"\n{doc.summary}\n"
                if tags:
                    msg += f"\n🏷 {tags}"

                await bot.send_message(
                    chat_id=user.telegram_id, text=msg, parse_mode="HTML"
                )
            except Exception as e:
                logger.error(
                    "Failed to send digest to user %s: %s", user.telegram_id, e
                )


async def send_weekly_digest(bot: Bot) -> None:
    """Weekly summary: what you saved this week, top tags, total growth."""
    async with async_session() as session:
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
