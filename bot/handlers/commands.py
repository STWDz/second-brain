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
        "🧠 <b>Second Brain</b> — твой интеллектуальный ассистент.\n\n"
        "<b>📥 Что сохраняю:</b>\n"
        "• Ссылки на статьи → выжимка\n"
        "• YouTube-видео → суть из субтитров\n"
        "• PDF-файлы → обработка документа\n"
        "• Голосовые/кружки → расшифровка\n"
        "• Фото с подписью → заметка\n"
        "• 💬 Пересланные сообщения → автозаметка\n\n"
        "<b>🔧 Команды:</b>\n"
        "• /ask <i>вопрос</i> — RAG-поиск по базе знаний\n"
        "• /search <i>слово</i> — текстовый поиск\n"
        "• /chat <i>текст</i> — свободный чат с ИИ\n"
        "• /quiz — проверь себя по заметкам\n"
        "• /random — случайная заметка\n"
        "• /pinned — закреплённые заметки\n"
        "• /stats — твоя статистика\n"
        "• /export — экспорт в Markdown\n"
        "• /tags — все теги\n\n"
        f"💡 <b>Inline-режим:</b> набери <code>@{bot_username} запрос</code> в любом чате!"
    )

    keyboard = None
    if settings.webapp_url:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📚 Мои материалы",
                        web_app=WebAppInfo(url=settings.webapp_url),
                    )
                ]
            ]
        )

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    text = (
        "📖 <b>Команды:</b>\n\n"
        "<b>🔍 Поиск:</b>\n"
        "/ask <i>вопрос</i> — AI-поиск по базе (RAG)\n"
        "/search <i>слово</i> — текстовый поиск\n\n"
        "<b>🧠 AI:</b>\n"
        "/chat <i>текст</i> — свободный чат с ИИ\n"
        "/quiz — квиз по заметкам\n\n"
        "<b>📚 Заметки:</b>\n"
        "/random — случайная заметка\n"
        "/pinned — закреплённые заметки\n"
        "/export — экспорт в Markdown\n"
        "/tags — все теги\n"
        "/stats — статистика мозга\n\n"
        "<b>📥 Отправь:</b>\n"
        "• Ссылку / YouTube / PDF / голосовое / кружок\n"
        "• Фото с подписью\n"
        "• Текст (>20 символов) или «заметка: ...»\n"
        "• Перешли любое сообщение\n\n"
        "<b>💡 Inline:</b> @botname запрос — ищи из любого чата\n"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("ask"))
async def cmd_ask(message: types.Message, command: CommandObject) -> None:
    question = command.args
    if not question:
        await message.answer("Используй: /ask <i>твой вопрос</i>", parse_mode="HTML")
        return

    wait_msg = await message.answer("🔍 Ищу в твоей базе знаний...")

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
        await message.answer("У тебя пока нет тегов. Сохрани что-нибудь!")
        return

    text = "🏷 <b>Твои теги:</b>\n\n" + "  ".join(tags)
    await message.answer(text, parse_mode="HTML")
