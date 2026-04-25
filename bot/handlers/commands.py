"""Command handlers: /start, /ask, /search, /tags, /help."""

import logging

from aiogram import Router, types
from aiogram.filters import Command, CommandObject

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories import get_or_create_user, get_user_tags
from bot.keyboards import main_menu
from bot.services.formatting import send_llm_response, tg_escape
from bot.services.openai_client import ask_with_context
from bot.services.rag import format_context_for_prompt, retrieve_hits, unique_sources

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("start"))
async def cmd_start(message: types.Message) -> None:
    async with async_session() as session:
        await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        await session.commit()

    bot_me = await message.bot.me()
    bot_username = bot_me.username

    # Keep the welcome short — the reply keyboard already exposes the main
    # actions visually. Advanced users can still type commands directly.
    text = (
        "🧠 <b>Cortex</b> — твій другий мозок у Telegram.\n\n"
        "<b>Як це працює:</b>\n"
        "1️⃣ Надсилай мені <b>посилання</b>, <b>PDF</b>, <b>YouTube</b> або <b>голосове</b>\n"
        "2️⃣ Я роблю саммарі + витягаю теги\n"
        "3️⃣ Пізніше натискай <b>🧠 Спитати базу</b> — знайду по твоїх нотатках\n\n"
        "<b>Меню нижче ⬇️</b> — найчастіші дії в один тап.\n"
        "Повний список: /help · приховати меню: /menu_off · показати знов: /menu\n\n"
        f"💡 Inline: <code>@{bot_username} запит</code> у будь-якому чаті."
    )

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=main_menu(settings.webapp_url or None),
    )


@router.message(Command("menu_off"))
async def cmd_menu_off(message: types.Message) -> None:
    """Hide the reply keyboard for users who prefer pure slash-commands."""
    from aiogram.types import ReplyKeyboardRemove

    await message.answer(
        "Меню приховано. Поверни командою /menu.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    max_chars = f"{settings.max_content_chars:,}".replace(",", " ")
    text = (
        "📖 <b>Інструкція Cortex</b>\n\n"
        "<b>🚀 Швидкий старт:</b>\n"
        "Кинь мені <b>посилання</b>, залий <b>PDF</b>, відправ <b>голосове</b> або <b>перешли повідомлення</b>. "
        "Я збережу це в твоїй особистій базі знань і зроблю саммарі.\n\n"
        "<b>⬇️ Меню (базові дії одним тапом):</b>\n"
        "🧠 <b>Спитати базу</b> — RAG-пошук по твоїх нотатках\n"
        "🔍 <b>Пошук</b> — швидкий текстовий пошук\n"
        "🎲 <b>Випадкова</b> — одна стара нотатка\n"
        "🧩 <b>Квіз</b> — AI-вікторина по твоїх матеріалах\n"
        "📌 <b>Закріплені</b> · 📊 <b>Статистика</b>\n"
        "🏷 <b>Теги</b> · 💬 <b>AI-чат</b> (без бази)\n\n"
        "<b>� Розширені команди:</b>\n"
        "/conspect <i>текст</i> — структурований конспект\n"
        "/tts <i>текст</i> — озвучити (або reply → озвучить повідомлення)\n"
        "/export — один великий .md файл\n"
        "/export_obsidian — ZIP з .md для Obsidian vault\n"
        "/menu — показати меню, /menu_off — приховати\n\n"
        "<b>🔗 Notion (опціонально):</b>\n"
        "/notion_connect <i>token db_url</i> — підключити Notion\n"
        "/notion_sync — вивантажити нотатки у Notion\n"
        "/notion_status — стан підключення\n\n"
        "<b>📥 Що можна надіслати:</b>\n"
        "• 🔗 Посилання на статтю — витягну текст і зроблю саммарі\n"
        "• 📺 YouTube — витягну субтитри (uk/ru/en)\n"
        "• 📄 PDF — прочитаю текст (не зашифрований, до 20 МБ)\n"
        "• 🎙 Голосове / ⭕ кружок — розшифрую Whisper-ом\n"
        "• 📸 Фото з підписом — збережу як нотатку\n"
        "• 💬 Переслане повідомлення — авто-нотатка\n"
        "• «нотатка: ...» — ручна нотатка\n"
        "• Просто текст — відповім як ІІ\n\n"
        "<b>⚠️ Ліміти:</b>\n"
        "• Максимальний розмір файлу — 20 МБ (ліміт Telegram Bot API)\n"
        f"• Контент з одного джерела обробляється до {max_chars} символів\n"
        f"• {settings.max_url_per_hour} посилань на годину\n\n"
        "<b>💡 Inline:</b> у будь-якому чаті напиши <code>@botname запит</code> — знайду в твоїй базі.\n"
    )
    await message.answer(text, parse_mode="HTML")


def _format_sources_footer(hits) -> str:
    """Build a human-readable footer with the sources used for the answer."""
    if not hits:
        return ""
    lines = ["", "📖 <b>Джерела</b>"]
    emoji_by_type = {
        "url": "🔗",
        "youtube": "📺",
        "pdf": "📄",
        "voice": "🎙",
        "text": "📝",
    }
    for hit in hits:
        icon = emoji_by_type.get(hit.source_type, "📄")
        title = hit.document_title or hit.source_url or f"документ #{hit.document_id}"
        # Truncate very long titles so the message stays compact
        if len(title) > 80:
            title = title[:77] + "..."
        safe_title = tg_escape(title)
        if hit.source_url and hit.source_type in {"url", "youtube"}:
            # Only allow http(s) links in href (paranoid — source_url is user-provided)
            url = hit.source_url
            if url.startswith(("http://", "https://")):
                lines.append(f'{icon} <a href="{tg_escape(url)}">{safe_title}</a>')
            else:
                lines.append(f"{icon} <i>{safe_title}</i>")
        else:
            lines.append(f"{icon} <i>{safe_title}</i>")
    return "\n".join(lines)


async def answer_ask(message: types.Message, question: str) -> None:
    """Core RAG-ask logic. Called by both /ask and the menu button.

    Kept as a plain helper so the menu handler can reuse it after FSM input
    without faking a CommandObject.
    """
    question = (question or "").strip()
    if not question:
        await message.answer(
            "Використовуй: /ask <i>твоє питання</i>\n\n"
            "Приклад: <code>/ask що я зберігав про маркетинг?</code>",
            parse_mode="HTML",
        )
        return

    wait_msg = await message.answer("🔍 Шукаю в твоїй базі знань...")

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        hits = await retrieve_hits(session, user.id, question)

    if not hits:
        await wait_msg.delete()
        await message.answer(
            "📭 У твоїй базі знань ще немає матеріалів, що відповідають на це питання.\n\n"
            "Надішли боту посилання, PDF, YouTube-відео або голосове — і спробуй знов.",
            parse_mode="HTML",
        )
        return

    context = format_context_for_prompt(hits)
    answer = await ask_with_context(question, context)
    sources_footer = _format_sources_footer(unique_sources(hits))

    await wait_msg.delete()
    await send_llm_response(message, answer + "\n" + sources_footer)


@router.message(Command("ask"))
async def cmd_ask(message: types.Message, command: CommandObject) -> None:
    await answer_ask(message, command.args or "")


@router.message(Command("tags"))
async def cmd_tags(message: types.Message) -> None:
    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        tags = await get_user_tags(session, user.id)

    if not tags:
        await message.answer("У тебе поки немає тегів. Збережи щось!")
        return

    text = "🏷 <b>Твої теги:</b>\n\n" + "  ".join(tags)
    await message.answer(text, parse_mode="HTML")
