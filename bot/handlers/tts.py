"""Text-to-speech commands and callbacks.

- /tts <text>        — synthesize arbitrary text into a voice message
- /tts (as reply)    — synthesize the replied-to message
- callback "tts:<doc_id>" — synthesize a saved note's summary
"""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile

from bot.db.engine import async_session
from bot.db.repositories import get_document_by_id, get_or_create_user
from bot.services.tts import synthesize

logger = logging.getLogger(__name__)
router = Router()

# Telegram caps voice messages at 1 MB for bots historically; edge-tts Opus
# is compact but we still want a sane upper bound on text length.
MAX_TTS_CHARS = 2500


@router.message(Command("tts"))
async def cmd_tts(message: types.Message, command: CommandObject) -> None:
    text = command.args
    if not text and message.reply_to_message:
        text = message.reply_to_message.text or message.reply_to_message.caption or ""

    text = (text or "").strip()
    if not text:
        await message.answer(
            "🔊 Використовуй: /tts <i>текст</i>\n\n"
            "Або відповідж командою /tts на будь-яке повідомлення — озвучу його.",
            parse_mode="HTML",
        )
        return

    if len(text) > MAX_TTS_CHARS:
        await message.answer(
            f"📏 Текст задовгий для озвучення ({len(text)} символів). "
            f"Максимум — {MAX_TTS_CHARS}. Скорочуй або розбий на частини."
        )
        return

    wait_msg = await message.answer("🎙 Генерую голос...")
    try:
        audio_bytes = await synthesize(text)
    except Exception as e:
        logger.exception("TTS failed: %s", e)
        await wait_msg.delete()
        await message.answer("❌ Не вдалося згенерувати голос. Спробуй ще раз.")
        return

    await wait_msg.delete()
    voice = BufferedInputFile(audio_bytes, filename="voice.ogg")
    await message.answer_voice(voice)


@router.callback_query(F.data.startswith("tts:"))
async def cb_tts_document(callback: types.CallbackQuery) -> None:
    """Synthesize the summary of a saved note (button appears under saved docs)."""
    try:
        _, doc_id_str, owner_str = callback.data.split(":", 2)
        doc_id = int(doc_id_str)
        owner_id = int(owner_str)
    except (ValueError, AttributeError):
        await callback.answer("Невалідний запит", show_alert=True)
        return

    if callback.from_user.id != owner_id:
        await callback.answer(
            "🔒 Це нотатка іншого користувача. Відкрий свою /random, щоб озвучити.",
            show_alert=True,
        )
        return

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        doc = await get_document_by_id(session, doc_id)

    if not doc or doc.user_id != user.id or not doc.summary:
        await callback.answer("Нема що озвучувати", show_alert=True)
        return

    await callback.answer("🎙 Генерую голос...")
    try:
        audio_bytes = await synthesize(doc.summary[:MAX_TTS_CHARS])
    except Exception as e:
        logger.exception("TTS (doc %d) failed: %s", doc_id, e)
        await callback.message.answer("❌ Не вдалося згенерувати голос.")
        return

    voice = BufferedInputFile(audio_bytes, filename="voice.ogg")
    await callback.message.answer_voice(voice)
