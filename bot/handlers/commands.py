"""Command handlers: /start, /ask, /search, /tags, /help."""

import logging

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories import get_or_create_user, get_user_tags
from bot.services.formatting import send_llm_response
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

    text = (
        "🧠 <b>Cortex</b> — твій другий мозок у Telegram.\n"
        "Я зберігаю інформацію, роблю з неї саммарі та відповідаю на питання по твоїй базі знань.\n\n"
        "<b>🚀 З чого почати — 3 кроки:</b>\n"
        "1️⃣ Надішли боту <b>посилання</b>, <b>PDF</b>, <b>YouTube</b> або <b>голосове</b>.\n"
        "2️⃣ Я зроблю саммарі, витягну ключові інсайти та теги.\n"
        "3️⃣ Пізніше спитай: <code>/ask про що була та стаття?</code> — я знайду відповідь у твоїй базі та вкажу джерело.\n\n"
        "<b>📥 Що я вмію приймати:</b>\n"
        "• 🔗 Посилання на статті → витяг + саммарі\n"
        "• 📺 YouTube-відео → суть із субтитрів\n"
        "• 📄 PDF-файли (до 20 МБ) → обробка\n"
        "• 🎙 Голосові / ⭕ кружки → розшифровка\n"
        "• 📸 Фото з підписом → нотатка\n"
        "• 💬 Переслані повідомлення → автонотатка\n\n"
        "<b>🔧 Основні команди:</b>\n"
        "/ask <i>питання</i> — RAG-пошук по твоїй базі (з посиланням на джерело)\n"
        "/search <i>слово</i> — швидкий текстовий пошук\n"
        "/conspect <i>текст</i> — структурований конспект\n"
        "/quiz — перевір себе по нотатках\n"
        "/random — випадкова нотатка для натхнення\n"
        "/pinned — закріплені\n"
        "/stats — твоя статистика\n"
        "/export — експорт у Markdown\n"
        "/help — повний список команд\n\n"
        "💬 Просто напиши мені будь-що — відповім як ІІ.\n\n"
        f"💡 <b>Inline-режим:</b> набери <code>@{bot_username} запит</code> у будь-якому чаті!"
    )

    keyboard = None
    if settings.webapp_url:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📚 Мої матеріали",
                        web_app=WebAppInfo(url=settings.webapp_url),
                    )
                ]
            ]
        )

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    max_chars = f"{settings.max_content_chars:,}".replace(",", " ")
    text = (
        "📖 <b>Інструкція Cortex</b>\n\n"
        "<b>🚀 Швидкий старт:</b>\n"
        "Кинь мені <b>посилання</b>, залий <b>PDF</b>, відправ <b>голосове</b> або <b>перешли повідомлення</b>. \n"
        "Я збережу це в твоїй особистій базі знань і зроблю саммарі.\n\n"
        "<b>🔍 Пошук по базі:</b>\n"
        "/ask <i>питання</i> — ІІ шукає відповідь по твоїх нотатках і вказує джерело\n"
        "/search <i>слово</i> — швидкий текстовий пошук\n\n"
        "<b>🧠 AI-інструменти:</b>\n"
        "/conspect <i>текст</i> — структурований конспект\n"
        "/quiz — квіз по твоїх нотатках\n"
        "/chat <i>запит</i> — вільний чат без бази знань\n"
        "/tts <i>текст</i> — озвучити текст (або reply → озвучить повідомлення)\n\n"
        "<b>📚 Управління нотатками:</b>\n"
        "/random — випадкова нотатка\n"
        "/pinned — закріплені\n"
        "/tags — всі твої теги\n"
        "/stats — статистика\n"
        "/export — один великий .md файл\n"
        "/export_obsidian — ZIP з .md для Obsidian vault\n\n"
        "<b>🔗 Інтеграції:</b>\n"
        "/notion_connect <i>token db_url</i> — підключити Notion\n"
        "/notion_sync — вивантажити нотатки у свій Notion\n"
        "/notion_status — перевірити стан підключення\n\n"
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
        "<b>💡 Inline-режим:</b> у будь-якому чаті напиши <code>@botname запит</code> — знайду в твоїй базі.\n"
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
        if hit.source_url and hit.source_type in {"url", "youtube"}:
            lines.append(f"{icon} <a href=\"{hit.source_url}\">{title}</a>")
        else:
            lines.append(f"{icon} <i>{title}</i>")
    return "\n".join(lines)


@router.message(Command("ask"))
async def cmd_ask(message: types.Message, command: CommandObject) -> None:
    question = command.args
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
