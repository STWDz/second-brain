"""Command handlers: /start, /ask, /search, /tags, /help."""

import logging

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories import get_or_create_user, get_user_tags
from bot.services.openai_client import ask_with_context
from bot.services.rag import retrieve_context

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
        "🧠 <b>Cortex</b> — твій інтелектуальний асистент.\n\n"
        "<b>📥 Що зберігаю:</b>\n"
        "• Посилання на статті → витяг\n"
        "• YouTube-відео → суть із субтитрів\n"
        "• PDF-файли → обробка документа\n"
        "• Голосові/кружки → розшифровка\n"
        "• Фото з підписом → нотатка\n"
        "• 💬 Переслані повідомлення → автонотатка\n\n"
        "<b>🔧 Команди:</b>\n"
        "• /ask <i>питання</i> — RAG-пошук по базі знань\n"
        "• /search <i>слово</i> — текстовий пошук\n"
        "• /conspect <i>текст</i> — конспект з тексту\n"
        "• /quiz — перевір себе по нотатках\n"
        "• /random — випадкова нотатка\n"
        "• /pinned — закріплені нотатки\n"
        "• /stats — твоя статистика\n"
        "• /export — експорт в Markdown\n"
        "• /tags — всі теги\n\n"
        "💬 Просто напиши — відповім як ІІ!\n\n"
        f"💡 <b>Inline-режим:</b> набери <code>@{bot_username} запит</code> в будь-якому чаті!"
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
    text = (
        "📖 <b>Команди:</b>\n\n"
        "<b>🔍 Пошук:</b>\n"
        "/ask <i>питання</i> — AI-пошук по базі (RAG)\n"
        "/search <i>слово</i> — текстовий пошук\n\n"
        "<b>🧠 AI:</b>\n"
        "/conspect <i>текст</i> — конспект з тексту\n"
        "/quiz — квіз по нотатках\n\n"
        "<b>📚 Нотатки:</b>\n"
        "/random — випадкова нотатка\n"
        "/pinned — закріплені нотатки\n"
        "/export — експорт в Markdown\n"
        "/tags — всі теги\n"
        "/stats — статистика мозку\n\n"
        "<b>📥 Надішли:</b>\n"
        "• Посилання / YouTube / PDF / голосове / кружок\n"
        "• Фото з підписом\n"
        "• Просто напиши — відповім як ІІ\n"
        "• «нотатка: ...» → збережеться\n"
        "• Перешли будь-яке повідомлення\n\n"
        "<b>💡 Inline:</b> @botname запит — шукай з будь-якого чату\n"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("ask"))
async def cmd_ask(message: types.Message, command: CommandObject) -> None:
    question = command.args
    if not question:
        await message.answer("Використовуй: /ask <i>твоє питання</i>", parse_mode="HTML")
        return

    wait_msg = await message.answer("🔍 Шукаю в твоїй базі знань...")

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        context = await retrieve_context(session, user.id, question)

    if not context:
        answer = await ask_with_context(question, context="(база знаний пуста)")
    else:
        answer = await ask_with_context(question, context)

    await wait_msg.delete()
    await message.answer(answer, parse_mode="HTML")


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
