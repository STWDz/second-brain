"""Voice & video_note (кружки) handler — Whisper transcription → note."""

import logging

from aiogram import F, Router, types

from bot.db.engine import async_session
from bot.db.repositories import create_document, get_or_create_user
from bot.services.openai_client import generate_tags, summarize_text, transcribe_voice
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
    """Common pipeline for voice messages and video notes (кружки)."""
    wait_msg = await message.answer(f"{icon} Расшифровываю {label}...")

    try:
        transcript = await transcribe_voice(file_bytes, filename=filename)
        if not transcript.strip():
            await wait_msg.delete()
            await message.answer("❌ Не удалось распознать речь.")
            return

        summary = await summarize_text(transcript)
        tags = await generate_tags(transcript)

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
                summary=summary,
                tags=tags,
            )
            chunk_count = await embed_and_store_chunks(session, doc.id, transcript)
            await session.commit()

        tags_str = " ".join(tags) if tags else "—"
        result_text = (
            f"✅ <b>{label.capitalize()} сохранено!</b>\n\n"
            f"📝 <b>Расшифровка:</b>\n{transcript[:500]}"
            f"{'...' if len(transcript) > 500 else ''}\n\n"
            f"{summary}\n\n"
            f"🏷 {tags_str}\n"
            f"📦 {chunk_count} фрагментов добавлено в базу знаний"
        )
        await wait_msg.delete()
        await message.answer(result_text, parse_mode="HTML")

    except Exception as e:
        logger.exception("Error processing %s: %s", label, e)
        await wait_msg.delete()
        await message.answer(f"❌ Ошибка при обработке {label}.")


@router.message(F.voice)
async def handle_voice(message: types.Message) -> None:
    """Transcribe a voice message and save as a structured note."""
    file = await message.bot.download(message.voice)
    await _process_audio(message, file.read(), "voice.ogg", "🎙", "голосовое сообщение")


@router.message(F.video_note)
async def handle_video_note(message: types.Message) -> None:
    """Transcribe a video note (кружок) and save as a structured note."""
    file = await message.bot.download(message.video_note)
    await _process_audio(message, file.read(), "video_note.mp4", "⭕", "видеокружок")
