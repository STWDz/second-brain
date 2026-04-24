"""Content extraction from various sources: URLs, YouTube, PDFs, voice."""

import io
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import fitz  # PyMuPDF
import trafilatura
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

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

# ── SSRF protection ────────────────────────────────────────────────────────

BLOCKED_SCHEMES = {"file", "ftp", "gopher", "data", "javascript"}
MAX_URL_LENGTH = 2048


def _is_url_safe(url: str) -> bool:
    """Validate URL to prevent SSRF attacks."""
    if len(url) > MAX_URL_LENGTH:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    # Only allow http/https
    if parsed.scheme not in ("http", "https"):
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    # Block localhost and private networks
    try:
        addr = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in addr:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                logger.warning("SSRF blocked: %s resolves to private IP %s", hostname, ip)
                return False
    except (socket.gaierror, ValueError):
        # Can't resolve — let trafilatura handle the error
        pass

    return True


def extract_youtube_id(url: str) -> Optional[str]:
    match = YOUTUBE_REGEX.search(url)
    return match.group(1) if match else None


async def extract_from_url(url: str) -> ExtractResult:
    """Extract clean text from a web article using trafilatura."""
    if not _is_url_safe(url):
        logger.warning("URL blocked by SSRF filter: %s", url[:200])
        return ExtractResult(
            error_code="unsafe",
            error_message="Це посилання заблоковане з міркувань безпеки.",
        )

    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded is None:
            return ExtractResult(
                error_code="unreachable",
                error_message="Не вдалося відкрити сторінку — можливо, вона недоступна або потребує авторизації.",
            )
        text = trafilatura.extract(
            downloaded, include_links=False, include_images=False
        )
        if not text or not text.strip():
            return ExtractResult(
                error_code="empty",
                error_message="На сторінці не вийшло знайти читомного тексту (сайт може бути захищений JS/paywall).",
            )
        return ExtractResult(text=text)
    except Exception as e:
        logger.error("Failed to extract URL %s: %s", url, e)
        return ExtractResult(
            error_code="fetch_error",
            error_message="Не вдалося завантажити сторінку. Перевір посилання.",
        )


async def extract_from_youtube(url: str) -> ExtractResult:
    """Extract transcript from a YouTube video with specific error reasons."""
    video_id = extract_youtube_id(url)
    if not video_id:
        return ExtractResult(
            error_code="bad_url",
            error_message="Це не схоже на посилання YouTube.",
        )
    try:
        api = YouTubeTranscriptApi()
        result = api.fetch(video_id, languages=["uk", "ru", "en"])
        text = " ".join(snippet.text for snippet in result.snippets)
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


async def extract_from_pdf(file_bytes: bytes) -> ExtractResult:
    """Extract text from a PDF file using PyMuPDF with specific error reasons."""
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
            doc.close()
            return ExtractResult(
                error_code="encrypted",
                error_message="Цей PDF зашифрований/захищений паролем — не можу його прочитати.",
            )
        text_parts: list[str] = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
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


def detect_source_type(text: str) -> str:
    """Detect source type from a URL or text."""
    if YOUTUBE_REGEX.search(text):
        return "youtube"
    if text.startswith("http://") or text.startswith("https://"):
        return "url"
    return "text"
