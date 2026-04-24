"""Inline mode — search your brain from any Telegram chat.
Usage: @botname search query
"""

import hashlib
import json
import logging
import time
from collections import defaultdict

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from bot.db.engine import async_session
from bot.db.repositories import get_or_create_user, search_documents_text
from bot.services.formatting import tg_escape

logger = logging.getLogger(__name__)
router = Router()

TYPE_EMOJI = {
    "url": "🔗",
    "youtube": "📺",
    "pdf": "📄",
    "voice": "🎙",
    "text": "📝",
}


# ── Rate limit for inline queries ──────────────────────────────────────────
# Dispatcher-level middlewares don't see InlineQuery, so we limit it here.
_INLINE_MAX_PER_MIN = 30
_inline_hits: dict[int, list[float]] = defaultdict(list)


def _inline_rate_limited(user_id: int) -> bool:
    now = time.time()
    hits = [t for t in _inline_hits[user_id] if now - t < 60]
    _inline_hits[user_id] = hits
    if len(hits) >= _INLINE_MAX_PER_MIN:
        return True
    _inline_hits[user_id].append(now)
    # Periodic GC of stale entries
    if len(_inline_hits) > 2000:
        for uid in list(_inline_hits):
            if not _inline_hits[uid] or now - _inline_hits[uid][-1] > 300:
                _inline_hits.pop(uid, None)
    return False


@router.inline_query()
async def inline_search(query: InlineQuery) -> None:
    """Handle inline queries — search user's knowledge base."""
    if _inline_rate_limited(query.from_user.id):
        await query.answer(results=[], cache_time=5, is_personal=True)
        return

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

        content_text = f"{emoji} <b>{tg_escape(title)}</b>\n\n{tg_escape(summary)}"
        if tags:
            content_text += f"\n\n🏷 {tg_escape(tags)}"
        if doc.source_url:
            content_text += f"\n\n🔗 {tg_escape(doc.source_url)}"

        # Unique ID for each result (MD5 is fine — not used for security)
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
        safe_query = tg_escape(text)
        results.append(
            InlineQueryResultArticle(
                id="empty",
                title="🔍 Ничего не найдено",
                description=f"По запросу «{text}» нет результатов",
                input_message_content=InputTextMessageContent(
                    message_text=f"🔍 По запросу «{safe_query}» ничего не найдено в моей базе знаний.",
                    parse_mode="HTML",
                ),
            )
        )

    await query.answer(results=results, cache_time=10, is_personal=True)
