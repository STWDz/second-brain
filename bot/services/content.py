"""Content extraction from various sources: URLs, YouTube, PDFs, voice."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

import fitz  # PyMuPDF
import trafilatura
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from bot.services.http_fetch import fetch_url_safe

logger = logging.getLogger(__name__)


@dataclass
class ExtractResult:
    """Result of a content extraction attempt."""
    text: Optional[str] = None
    error_code: Optional[str] = None  # e.g. "encrypted", "no_subtitles", "unreachable"
    error_message: Optional[str] = None  # human-readable

    @property
    def ok(self) -> bool:
        return self.text is not None and bool(self.text.strip())

YOUTUBE_REGEX = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/)([\w-]{11})"
)

# PDF hard-limits (defence in depth; Telegram already caps uploads at 20 MB)
MAX_PDF_PAGES = 300
MAX_PDF_TEXT = 2 * 1024 * 1024  # 2 MB of extracted text


def extract_youtube_id(url: str) -> Optional[str]:
    match = YOUTUBE_REGEX.search(url)
    return match.group(1) if match else None


async def extract_from_url(url: str) -> ExtractResult:
    """Extract clean article text via SSRF-hardened fetcher + trafilatura."""
    fetched = await fetch_url_safe(url)
    if not fetched.ok or fetched.body is None:
        return ExtractResult(
            error_code=fetched.error_code or "fetch_error",
            error_message=fetched.error_message
            or "Не вдалося завантажити сторінку.",
        )

    # Decode with charset detection (fall back to utf-8 with replace)
    try:
        html = fetched.body.decode("utf-8")
    except UnicodeDecodeError:
        html = fetched.body.decode("latin-1", errors="replace")

    try:
        text = trafilatura.extract(
            html, include_links=False, include_images=False, url=fetched.final_url
        )
    except Exception as e:
        logger.error("trafilatura.extract failed for %s: %s", fetched.final_url, e)
        return ExtractResult(
            error_code="extract_error",
            error_message="Не вдалося розібрати вміст сторінки.",
        )

    if not text or not text.strip():
        return ExtractResult(
            error_code="empty",
            error_message="На сторінці не вийшло знайти читомного тексту (сайт може бути захищений JS/paywall).",
        )
    return ExtractResult(text=text)


async def extract_from_youtube(url: str) -> ExtractResult:
    """Extract transcript from a YouTube video with specific error reasons.

    youtube_transcript_api is synchronous and does network I/O; we offload it
    to a worker thread so the bot's event loop stays responsive for other users.
    """
    video_id = extract_youtube_id(url)
    if not video_id:
        return ExtractResult(
            error_code="bad_url",
            error_message="Це не схоже на посилання YouTube.",
        )

    def _fetch_sync() -> str:
        api = YouTubeTranscriptApi()
        result = api.fetch(video_id, languages=["uk", "ru", "en"])
        return " ".join(snippet.text for snippet in result.snippets)

    try:
        text = await asyncio.to_thread(_fetch_sync)
        if not text.strip():
            return ExtractResult(
                error_code="empty",
                error_message="Субтитри є, але порожні.",
            )
        return ExtractResult(text=text)
    except TranscriptsDisabled:
        return ExtractResult(
            error_code="disabled",
            error_message="У цього відео вимкнені субтитри. На жаль, я не можу витягнути текст.",
        )
    except NoTranscriptFound:
        return ExtractResult(
            error_code="no_subtitles",
            error_message="У цього відео немає субтитрів мовами, які я підтримую (uk/ru/en).",
        )
    except VideoUnavailable:
        return ExtractResult(
            error_code="unavailable",
            error_message="Відео недоступне (приватне, видалене або з обмеженням регіону).",
        )
    except Exception as e:
        logger.error("Failed to extract YouTube %s: %s", video_id, e)
        return ExtractResult(
            error_code="unknown",
            error_message="Не вдалося отримати субтитри з YouTube.",
        )


def _extract_pdf_sync(file_bytes: bytes) -> ExtractResult:
    """CPU-bound PDF extraction. Caller wraps in asyncio.to_thread."""
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as e:
        logger.error("Failed to open PDF: %s", e)
        return ExtractResult(
            error_code="corrupt",
            error_message="Файл пошкоджений або це не PDF.",
        )

    try:
        if doc.needs_pass:
            return ExtractResult(
                error_code="encrypted",
                error_message="Цей PDF зашифрований/захищений паролем — не можу його прочитати.",
            )
        total_pages = doc.page_count
        if total_pages > MAX_PDF_PAGES:
            logger.info("Truncating PDF from %d to %d pages", total_pages, MAX_PDF_PAGES)
        text_parts: list[str] = []
        running_size = 0
        for idx, page in enumerate(doc):
            if idx >= MAX_PDF_PAGES:
                break
            piece = page.get_text() or ""
            running_size += len(piece)
            if running_size > MAX_PDF_TEXT:
                text_parts.append(piece[: max(0, MAX_PDF_TEXT - (running_size - len(piece)))])
                break
            text_parts.append(piece)
        text = "\n".join(text_parts).strip()
        if not text:
            return ExtractResult(
                error_code="empty",
                error_message="В PDF немає тексту — можливо, це скан/зображення без OCR.",
            )
        return ExtractResult(text=text)
    except Exception as e:
        logger.error("Failed to extract PDF: %s", e)
        return ExtractResult(
            error_code="extract_error",
            error_message="Не вдалося витягнути текст з PDF.",
        )
    finally:
        try:
            doc.close()
        except Exception:
            pass


async def extract_from_pdf(file_bytes: bytes) -> ExtractResult:
    """Extract text from a PDF with page & size caps. Offloaded to worker thread."""
    return await asyncio.to_thread(_extract_pdf_sync, file_bytes)


def detect_source_type(text: str) -> str:
    """Detect source type from a URL or text."""
    if YOUTUBE_REGEX.search(text):
        return "youtube"
    if text.startswith("http://") or text.startswith("https://"):
        return "url"
    return "text"
