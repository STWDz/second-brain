"""Voice & video_note (кружки) handler — Whisper transcription + save."""

import logging

from aiogram import F, Router, types

from bot.db.engine import async_session
from bot.db.repositories import create_document, get_or_create_user
from bot.services.openai_client import transcribe_voice
from bot.services.rag import embed_and_store_chunks

logger = logging.getLogger(__name__)
router = Router()


async def _process_audio(
    message: types.Message,
    file_bytes: bytes,
    filename: str,
    icon: str,
    label: str,
) -> None:
    """Transcribe voice/video note, save to DB, show only transcription."""
    wait_msg = await message.answer(f"{icon} Розшифровую {label}...")

    try:
        transcript = await transcribe_voice(file_bytes, filename=filename)
        if not transcript.strip():
            await wait_msg.delete()
            await message.answer("❌ Не вдалося розпізнати мовлення.")
            return

        async with async_session() as session:
            user = await get_or_create_user(
                session, telegram_id=message.from_user.id
            )
            doc = await create_document(
                session,
                user_id=user.id,
                title=f"{icon} {label.capitalize()}",
                source_url=None,
                source_type="voice",
                summary=transcript[:500],
                tags=[],
            )
            await embed_and_store_chunks(session, doc.id, transcript)
            await session.commit()

        await wait_msg.delete()
        await message.answer(
            f"📝 <b>Розшифровка:</b>\n\n{transcript}",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception("Error processing %s: %s", label, e)
        await wait_msg.delete()
        await message.answer(f"❌ Помилка при обробці {label}.")


@router.message(F.voice)
async def handle_voice(message: types.Message) -> None:
    """Transcribe a voice message — text only."""
    file = await message.bot.download(message.voice)
    await _process_audio(message, file.read(), "voice.ogg", "🎙", "голосове повідомлення")


@router.message(F.video_note)
async def handle_video_note(message: types.Message) -> None:
    """Transcribe a video note (кружок) — text only."""
    file = await message.bot.download(message.video_note)
    await _process_audio(message, file.read(), "video_note.mp4", "⭕", "відеокружок")
