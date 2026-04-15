"""Inline mode — search your brain from any Telegram chat.
Usage: @botname search query
"""

import hashlib
import json
import logging

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from bot.db.engine import async_session
from bot.db.repositories import get_or_create_user, search_documents_text

logger = logging.getLogger(__name__)
router = Router()

TYPE_EMOJI = {
    "url": "🔗",
    "youtube": "📺",
    "pdf": "📄",
    "voice": "🎙",
    "text": "📝",
}


@router.inline_query()
async def inline_search(query: InlineQuery) -> None:
    """Handle inline queries — search user's knowledge base."""
    text = query.query.strip()
    if len(text) < 2:
        await query.answer(
            results=[],
            cache_time=1,
            switch_pm_text="🧠 Введи запрос для поиска",
            switch_pm_parameter="inline_help",
        )
        return

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=query.from_user.id)
        docs = await search_documents_text(session, user.id, text, limit=10)

    results = []
    for doc in docs:
        emoji = TYPE_EMOJI.get(doc.source_type, "📄")
        title = doc.title or "Без названия"
        if len(title) > 60:
            title = title[:57] + "..."

        summary = doc.summary or "Нет описания"
        description = summary[:100].replace("\n", " ")

        tags = ""
        if doc.tags:
            try:
                tags = " ".join(json.loads(doc.tags))
            except (json.JSONDecodeError, TypeError):
                pass

        content_text = f"{emoji} <b>{title}</b>\n\n{summary}"
        if tags:
            content_text += f"\n\n🏷 {tags}"
        if doc.source_url:
            content_text += f"\n\n🔗 {doc.source_url}"

        # Unique ID for each result
        result_id = hashlib.md5(f"{doc.id}:{text}".encode()).hexdigest()

        results.append(
            InlineQueryResultArticle(
                id=result_id,
                title=f"{emoji} {title}",
                description=description,
                input_message_content=InputTextMessageContent(
                    message_text=content_text,
                    parse_mode="HTML",
                ),
            )
        )

    if not results:
        results.append(
            InlineQueryResultArticle(
                id="empty",
                title="🔍 Ничего не найдено",
                description=f"По запросу «{text}» нет результатов",
                input_message_content=InputTextMessageContent(
                    message_text=f"🔍 По запросу «{text}» ничего не найдено в моей базе знаний.",
                ),
            )
        )

    await query.answer(results=results, cache_time=10, is_personal=True)
