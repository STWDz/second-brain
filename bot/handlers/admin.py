"""Admin-only commands.

Gated by ADMIN_IDS env variable. Every command silently ignores non-admins so
ordinary users never even see the commands exist.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Router, types
from aiogram.exceptions import TelegramForbiddenError, TelegramNotFound
from aiogram.filters import Command, CommandObject

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories import (
    get_global_stats,
    list_all_telegram_ids,
    list_recent_users,
)

logger = logging.getLogger(__name__)
router = Router()


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_user_ids


# ── /admin ── Show available admin commands ───────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: types.Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    text = (
        "🛡 <b>Admin-панель</b>\n\n"
        "/admin_stats — глобальна статистика\n"
        "/admin_users [N] — останні N користувачів (default 20)\n"
        "/admin_broadcast <i>текст</i> — розсилка всім користувачам\n"
        "/admin_whoami — перевірка ID та прав"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("admin_whoami"))
async def cmd_admin_whoami(message: types.Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    await message.answer(
        f"👤 ID: <code>{message.from_user.id}</code>\n"
        f"🛡 Admin: ✅\n"
        f"📋 Admin-pool: {sorted(settings.admin_user_ids)}",
        parse_mode="HTML",
    )


@router.message(Command("admin_stats"))
async def cmd_admin_stats(message: types.Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    async with async_session() as session:
        stats = await get_global_stats(session)

    by_type_lines = [
        f"  • {t}: <b>{c}</b>" for t, c in sorted(stats["by_type"].items(), key=lambda x: -x[1])
    ]
    by_type_block = "\n".join(by_type_lines) or "  (порожньо)"

    text = (
        "📊 <b>Глобальна статистика</b>\n\n"
        f"👥 Користувачів: <b>{stats['users']}</b>\n"
        f"📄 Документів: <b>{stats['documents']}</b>\n"
        f"🧩 Чанків: <b>{stats['chunks']}</b>\n"
        f"🟢 Активних за 7 днів: <b>{stats['active_7d']}</b>\n\n"
        "<b>За типами:</b>\n"
        f"{by_type_block}"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("admin_users"))
async def cmd_admin_users(message: types.Message, command: CommandObject) -> None:
    if not _is_admin(message.from_user.id):
        return

    try:
        limit = int((command.args or "20").strip())
        limit = max(1, min(limit, 200))
    except ValueError:
        limit = 20

    async with async_session() as session:
        users = await list_recent_users(session, limit=limit)

    if not users:
        await message.answer("📭 Користувачів ще немає.")
        return

    lines = [f"👥 <b>Останні {len(users)} користувачів</b>\n"]
    for u in users:
        created = u.created_at.strftime("%d.%m.%Y") if u.created_at else ""
        uname = f"@{u.username}" if u.username else "—"
        fname = u.first_name or "—"
        lines.append(f"• <code>{u.telegram_id}</code> · {uname} · {fname} · {created}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("admin_broadcast"))
async def cmd_admin_broadcast(
    message: types.Message, command: CommandObject, bot: Bot
) -> None:
    if not _is_admin(message.from_user.id):
        return

    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "📣 Використовуй: /admin_broadcast <i>текст</i>\n\n"
            "HTML дозволений (&lt;b&gt;, &lt;i&gt;, &lt;code&gt;).",
            parse_mode="HTML",
        )
        return

    async with async_session() as session:
        recipient_ids = await list_all_telegram_ids(session)

    if not recipient_ids:
        await message.answer("📭 Немає кому розсилати.")
        return

    status = await message.answer(
        f"📣 Починаю розсилку для {len(recipient_ids)} користувачів..."
    )

    sent = 0
    failed = 0
    blocked = 0
    # Telegram rate limit: ~30 msg/s. We pace to 20 to be safe.
    for i, uid in enumerate(recipient_ids, 1):
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
        except TelegramForbiddenError:
            blocked += 1
        except TelegramNotFound:
            blocked += 1
        except Exception as e:
            failed += 1
            logger.warning("Broadcast to %d failed: %s", uid, e)
        if i % 20 == 0:
            await asyncio.sleep(1.0)

    await status.edit_text(
        f"📣 <b>Розсилка завершена</b>\n\n"
        f"✅ Доставлено: <b>{sent}</b>\n"
        f"🚫 Заблокували бота: <b>{blocked}</b>\n"
        f"⚠️ Помилок: <b>{failed}</b>",
        parse_mode="HTML",
    )
