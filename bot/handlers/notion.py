"""Notion integration commands.

- /notion_connect <token> <database_id_or_url>  — save credentials
- /notion_status                                — show current connection
- /notion_sync [N]                              — push last N (default all) notes
- /notion_disconnect                            — remove saved credentials
"""

from __future__ import annotations

import logging

from aiogram import Router, types
from aiogram.filters import Command, CommandObject

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories import get_or_create_user, get_user_documents
from bot.services.notion import (
    NotionError,
    decrypt_token,
    delete_integration,
    get_integration,
    normalize_database_id,
    push_documents,
    upsert_integration,
    verify_credentials,
)

logger = logging.getLogger(__name__)
router = Router()


def _ensure_enabled(message: types.Message) -> bool:
    if not settings.notion_enabled:
        return False
    return True


@router.message(Command("notion_connect"))
async def cmd_notion_connect(message: types.Message, command: CommandObject) -> None:
    if not _ensure_enabled(message):
        return

    args = (command.args or "").split()
    if len(args) < 2:
        await message.answer(
            "🔗 <b>Підключити Notion</b>\n\n"
            "1. Створи Internal Integration: https://www.notion.so/my-integrations\n"
            "2. Скопіюй <b>Internal Integration Token</b> (починається з <code>secret_</code> або <code>ntn_</code>).\n"
            "3. Створи базу даних у Notion (рекомендовані властивості: <i>Name</i> (title), "
            "<i>Source</i> (URL), <i>Tags</i> (multi-select), <i>Date</i> (date), <i>Type</i> (select)).\n"
            "4. Додай інтеграцію в налаштуваннях бази (<i>Connections</i> → твоя integration).\n"
            "5. Скопіюй URL бази даних.\n\n"
            "Потім виконай:\n"
            "<code>/notion_connect secret_xxxxx https://notion.so/...</code>\n\n"
            "⚠️ Токен буде зашифрований на сервері. Ніколи не показуй цей токен іншим.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    # Best-effort: delete the message with the token so it doesn't linger in chat
    try:
        await message.delete()
    except Exception:
        pass

    token, db_ref = args[0], args[1]

    try:
        database_id = normalize_database_id(db_ref)
    except ValueError as e:
        await message.answer(f"❌ {e}")
        return

    status = await message.answer("🔎 Перевіряю Notion credentials...")
    try:
        db_title = await verify_credentials(token, database_id)
    except NotionError as e:
        await status.edit_text(
            f"❌ Notion відхилив credentials: <i>{e.message}</i>\n\n"
            "Перевір, що інтеграція має доступ до бази (Share → Connections → Add).",
            parse_mode="HTML",
        )
        return
    except Exception as e:
        logger.exception("Notion verification failed: %s", e)
        await status.edit_text("❌ Мережева помилка при з'єднанні з Notion.")
        return

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        await upsert_integration(session, user.id, token=token, database_id=database_id)
        await session.commit()

    await status.edit_text(
        f"✅ <b>Notion підключено!</b>\n\n"
        f"📚 База: <b>{db_title}</b>\n"
        f"🆔 <code>{database_id}</code>\n\n"
        "Використай /notion_sync щоб вивантажити існуючі нотатки.",
        parse_mode="HTML",
    )


@router.message(Command("notion_status"))
async def cmd_notion_status(message: types.Message) -> None:
    if not _ensure_enabled(message):
        return
    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        integration = await get_integration(session, user.id)

    if integration is None:
        await message.answer(
            "🔌 Notion не підключено. Використай /notion_connect для налаштування."
        )
        return

    await message.answer(
        "✅ <b>Notion підключено</b>\n\n"
        f"🆔 База: <code>{integration.database_id}</code>\n"
        f"🕒 З'єднано: {integration.created_at.strftime('%d.%m.%Y') if integration.created_at else '—'}\n"
        f"🔁 Авто-синк нових нотаток: {'✅' if integration.auto_sync else '❌'}",
        parse_mode="HTML",
    )


@router.message(Command("notion_disconnect"))
async def cmd_notion_disconnect(message: types.Message) -> None:
    if not _ensure_enabled(message):
        return
    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        removed = await delete_integration(session, user.id)
        await session.commit()

    if removed:
        await message.answer("🔌 Notion відключено. Токен видалено з бази.")
    else:
        await message.answer("ℹ️ Notion і так не був підключений.")


@router.message(Command("notion_sync"))
async def cmd_notion_sync(message: types.Message, command: CommandObject) -> None:
    if not _ensure_enabled(message):
        return

    try:
        limit = int((command.args or "50").strip())
        limit = max(1, min(limit, 500))
    except ValueError:
        limit = 50

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        integration = await get_integration(session, user.id)
        if integration is None:
            await message.answer(
                "🔌 Notion не підключено. Спочатку виконай /notion_connect."
            )
            return
        docs = await get_user_documents(session, user.id, limit=limit)

    if not docs:
        await message.answer("📭 Нема що синхронізувати.")
        return

    token = decrypt_token(integration.token_encrypted)
    if token is None:
        await message.answer(
            "❌ Не вдалося розшифрувати токен. Переконект: /notion_disconnect → /notion_connect."
        )
        return

    status = await message.answer(
        f"⏳ Вивантажую в Notion: 0/{len(docs)}..."
    )
    try:
        success, failed = await push_documents(
            token, integration.database_id, list(docs)
        )
    except Exception as e:
        logger.exception("Notion sync failed: %s", e)
        await status.edit_text("❌ Помилка під час синку. Спробуй ще раз.")
        return

    await status.edit_text(
        f"✅ <b>Notion sync завершено</b>\n\n"
        f"📝 Вивантажено: <b>{success}</b>\n"
        f"⚠️ Помилок: <b>{failed}</b>",
        parse_mode="HTML",
    )
