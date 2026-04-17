"""Content extraction from various sources: URLs, YouTube, PDFs, voice."""

import io
import ipaddress
import logging
import re
import socket
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import fitz  # PyMuPDF
import trafilatura
from youtube_transcript_api import YouTubeTranscriptApi

logger = logging.getLogger(__name__)

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


async def extract_from_url(url: str) -> Optional[str]:
    """Extract clean text from a web article using trafilatura."""
    if not _is_url_safe(url):
        logger.warning("URL blocked by SSRF filter: %s", url[:200])
        return None

    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded is None:
            return None
        text = trafilatura.extract(
            downloaded, include_links=False, include_images=False
        )
        return text
    except Exception as e:
        logger.error("Failed to extract URL %s: %s", url, e)
        return None


async def extract_from_youtube(url: str) -> Optional[str]:
    """Extract transcript from a YouTube video."""
    video_id = extract_youtube_id(url)
    if not video_id:
        return None
    try:
        api = YouTubeTranscriptApi()
        result = api.fetch(video_id, languages=["uk", "ru", "en"])
        text = " ".join(snippet.text for snippet in result.snippets)
        return text
    except Exception as e:
        logger.error("Failed to extract YouTube %s: %s", video_id, e)
        return None


async def extract_from_pdf(file_bytes: bytes) -> Optional[str]:
    """Extract text from a PDF file using PyMuPDF."""
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text_parts: list[str] = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        text = "\n".join(text_parts).strip()
        return text if text else None
    except Exception as e:
        logger.error("Failed to extract PDF: %s", e)
        return None


def detect_source_type(text: str) -> str:
    """Detect source type from a URL or text."""
    if YOUTUBE_REGEX.search(text):
        return "youtube"
    if text.startswith("http://") or text.startswith("https://"):
        return "url"
    return "text"
