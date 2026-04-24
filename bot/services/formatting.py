"""Text formatting utilities for safely sending LLM output to Telegram.

Handles the common case where the model leaks Markdown (**bold**) despite
being told to use HTML, or produces malformed tags that Telegram would
reject with 400 Bad Request.
"""

from __future__ import annotations

import html as html_module
import logging
import re
from typing import Optional

from aiogram import types
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)


def tg_escape(text: Optional[str]) -> str:
    """HTML-escape user- or DB-controlled text for Telegram parse_mode='HTML'.

    Telegram only treats `<`, `>`, `&` specially inside text, and additionally
    `"` inside attribute values. We escape all four so strings like
    ``<script>``, ``a & b``, or ``"quote"`` never break message parsing.
    Use this on every interpolated field that came from the user, LLM, or DB.
    """
    if not text:
        return ""
    return html_module.escape(str(text), quote=True)

# Tags Telegram actually understands inside messages (HTML parse mode)
_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "s", "code", "pre", "a", "blockquote"}

_MD_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_MD_ITALIC = re.compile(r"(?<![\*\w])\*(?!\s)([^\*\n]+?)(?<!\s)\*(?![\*\w])")
_MD_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_MD_HR = re.compile(r"^\s*(?:-{3,}|_{3,}|\*{3,})\s*$", re.MULTILINE)
_TAG = re.compile(r"<(/?)([a-zA-Z]+)(\s[^>]*)?>")


def _strip_markdown_artifacts(text: str) -> str:
    """Convert the most common Markdown sprinkles to Telegram HTML."""
    # Headers -> bold line
    text = _MD_HEADER.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    # Horizontal rules -> blank line
    text = _MD_HR.sub("", text)
    # Bold **x** -> <b>x</b>
    text = _MD_BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    # Inline code `x` -> <code>x</code>
    text = _MD_INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    # Italic *x* -> <i>x</i> (after bold so ** is already gone)
    text = _MD_ITALIC.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    return text


def _strip_unknown_tags(text: str) -> str:
    """Remove any HTML tags Telegram does not accept (p, div, ul, li, ...)."""

    def _replace(match: re.Match) -> str:
        tag_name = match.group(2).lower()
        if tag_name in _ALLOWED_TAGS:
            return match.group(0)
        return ""

    return _TAG.sub(_replace, text)


def _strip_all_html(text: str) -> str:
    """Drop every HTML tag and un-escape the common entities for plain fallback."""
    text = _TAG.sub("", text)
    return (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )


def clean_llm_html(text: str) -> str:
    """Normalize LLM output into Telegram-safe HTML.

    Converts stray Markdown and strips tags Telegram cannot parse.
    """
    if not text:
        return text
    text = _strip_markdown_artifacts(text)
    text = _strip_unknown_tags(text)
    return text.strip()


def _chunk(text: str, limit: int = 4000) -> list[str]:
    """Split a long message into Telegram-friendly chunks on paragraph breaks."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def send_llm_response(
    message: types.Message,
    text: str,
    reply_markup: Optional[types.InlineKeyboardMarkup] = None,
) -> Optional[types.Message]:
    """Send LLM output with HTML, fall back to plain text if Telegram rejects it.

    Returns the last message sent (or None if nothing was sent).
    """
    cleaned = clean_llm_html(text) or "(пусто)"
    chunks = _chunk(cleaned)
    last: Optional[types.Message] = None
    for idx, chunk in enumerate(chunks):
        markup = reply_markup if idx == len(chunks) - 1 else None
        try:
            last = await message.answer(chunk, parse_mode="HTML", reply_markup=markup)
        except TelegramBadRequest as e:
            logger.warning("HTML parse failed, falling back to plain text: %s", e)
            plain = _strip_all_html(chunk)
            last = await message.answer(plain, reply_markup=markup)
    return last
