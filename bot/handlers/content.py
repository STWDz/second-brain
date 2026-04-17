"""Handlers for URL links, PDF documents, photos, and plain-text notes."""

import asyncio
import logging

from aiogram import F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db.engine import async_session
from bot.db.repositories import create_document, get_or_create_user
from bot.services.content import (
    detect_source_type,
    extract_from_pdf,
    extract_from_url,
    extract_from_youtube,
)
from bot.services.openai_client import free_chat, generate_tags, summarize_text
from bot.services.rag import embed_and_store_chunks

logger = logging.getLogger(__name__)
router = Router()

PROGRESS_FRAMES = [
    "⏳ Обробляю ░░░░░░░░░░",
    "⏳ Обробляю █░░░░░░░░░",
    "⏳ Обробляю ██░░░░░░░░",
    "⏳ Обробляю ███░░░░░░░",
    "⏳ Обробляю ████░░░░░░",
    "⏳ Обробляю █████░░░░░",
    "⏳ Обробляю ██████░░░░",
    "⏳ Обробляю ███████░░░",
    "⏳ Обробляю ████████░░",
    "⏳ Обробляю █████████░",
    "✅ Готово! ██████████████",
]


async def _animate_progress(wait_msg: types.Message, stop_event: asyncio.Event) -> None:
    """Animate a progress bar while processing."""
    for i, frame in enumerate(PROGRESS_FRAMES[:-1]):
        if stop_event.is_set():
            return
        try:
            await wait_msg.edit_text(frame)
        except Exception:
            return
        await asyncio.sleep(1.2)
    # Hold at last frame
    while not stop_event.is_set():
        await asyncio.sleep(0.5)


async def _process_text_content(
    message: types.Message,
    text: str,
    source_url: str | None,
    source_type: str,
    title: str | None = None,
) -> None:
    """Common pipeline: summarize → tag → embed → store with animated progress."""
    wait_msg = await message.answer(PROGRESS_FRAMES[0])
    stop_event = asyncio.Event()
    progress_task = asyncio.create_task(_animate_progress(wait_msg, stop_event))

    try:
        summary = await summarize_text(text)
        tags = await generate_tags(text)

        async with async_session() as session:
            user = await get_or_create_user(
                session, telegram_id=message.from_user.id
            )
            doc = await create_document(
                session,
                user_id=user.id,
                title=title or (text[:80] + "..." if len(text) > 80 else text),
                source_url=source_url,
                source_type=source_type,
                summary=summary,
                tags=tags,
            )
            chunk_count = await embed_and_store_chunks(session, doc.id, text)
            await session.commit()
            doc_id = doc.id

        stop_event.set()
        await progress_task

        tags_str = " ".join(tags) if tags else "—"
        result_text = (
            f"✅ <b>Збережено!</b>\n\n"
            f"{summary}\n\n"
            f"🏷 {tags_str}\n"
            f"📦 {chunk_count} фрагментів у базі знань"
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🧒 Простіше",
                        callback_data=f"simplify:{doc_id}:{message.from_user.id}",
                    ),
                    InlineKeyboardButton(
                        text="📌 Закріпити",
                        callback_data=f"pin:{doc_id}:{message.from_user.id}",
                    ),
                    InlineKeyboardButton(
                        text="🗑 Видалити",
                        callback_data=f"del:{doc_id}:{message.from_user.id}",
                    ),
                ]
            ]
        )

        await wait_msg.delete()
        await message.answer(result_text, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        stop_event.set()
        await progress_task
        logger.exception("Error processing content: %s", e)
        await wait_msg.delete()
        await message.answer("❌ Помилка при обробці. Спробуй ще раз.")


@router.message(F.document)
async def handle_document(message: types.Message) -> None:
    """Handle PDF file uploads."""
    doc = message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".pdf"):
        await message.answer("Поки підтримую тільки PDF-файли.")
        return

    file = await message.bot.download(doc)
    file_bytes = file.read()
    text = await extract_from_pdf(file_bytes)

    if not text:
        await message.answer("❌ Не вдалося витягнути текст з PDF.")
        return

    await _process_text_content(
        message,
        text=text,
        source_url=None,
        source_type="pdf",
        title=doc.file_name,
    )


@router.message(F.forward_date, F.text)
async def handle_forwarded_text(message: types.Message) -> None:
    """Handle forwarded text messages — save as notes."""
    text = message.text.strip()
    if not text or len(text) < 10:
        return

    fwd_from = ""
    if message.forward_from:
        name = message.forward_from.first_name or message.forward_from.username or "?"
        fwd_from = f" від {name}"
    elif message.forward_sender_name:
        fwd_from = f" від {message.forward_sender_name}"

    await _process_text_content(
        message,
        text=text,
        source_url=None,
        source_type="text",
        title=f"💬 Переслане{fwd_from}",
    )


@router.message(F.text)
async def handle_text(message: types.Message) -> None:
    """Handle URLs and plain-text notes."""
    text = message.text.strip()

    # Skip commands
    if text.startswith("/"):
        return

    # Check if it's a note
    if text.lower().startswith(("нотатка:", "заметка:")):
        prefix = "нотатка:" if text.lower().startswith("нотатка:") else "заметка:"
        note_text = text[len(prefix):].strip()
        if not note_text:
            await message.answer("Напиши текст нотатки після «нотатка:»")
            return
        await _process_text_content(
            message,
            text=note_text,
            source_url=None,
            source_type="text",
            title=None,
        )
        return

    # Check source type
    source_type = detect_source_type(text)

    if source_type == "youtube":
        wait_yt = await message.answer("📺 Витягую субтитри з YouTube...")
        extracted = await extract_from_youtube(text)
        await wait_yt.delete()
        if not extracted:
            await message.answer(
                "❌ Не вдалося отримати субтитри з YouTube.\n"
                "Можливо, у цього відео немає субтитрів."
            )
            return
        await _process_text_content(
            message,
            text=extracted,
            source_url=text,
            source_type="youtube",
            title=f"📺 YouTube",
        )
        return

    if source_type == "url":
        extracted = await extract_from_url(text)
        if not extracted:
            await message.answer("❌ Не вдалося витягнути текст з посилання.")
            return
        await _process_text_content(
            message,
            text=extracted,
            source_url=text,
            source_type="url",
            title=None,
        )
        return

    # Plain text — always chat with AI
    # To save as note, use "заметка:" prefix
    if len(text) > 2:
        wait_msg = await message.answer("💭 Думаю...")
        try:
            answer = await free_chat(text)
            await wait_msg.delete()
            await message.answer(answer, parse_mode="HTML")
        except Exception as e:
            logger.exception("Chat error: %s", e)
            await wait_msg.delete()
            await message.answer("❌ Помилка. Спробуй ще раз.")


@router.message(F.photo)
async def handle_photo(message: types.Message) -> None:
    """Handle photos — use caption if present, otherwise note the image was received."""
    caption = message.caption or ""

    if caption and len(caption.strip()) > 10:
        await _process_text_content(
            message,
            text=caption.strip(),
            source_url=None,
            source_type="text",
            title="📸 Фото з підписом",
        )
    else:
        await message.answer(
            "📸 Фото отримано! Додай підпис до фото, щоб я зберіг його як нотатку.\n"
            "Наприклад, прикріпи фото і напиши опис до нього.",
            parse_mode="HTML",
        )
