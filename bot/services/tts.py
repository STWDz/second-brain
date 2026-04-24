"""Text-to-speech via edge-tts (Microsoft Edge cloud TTS, no API key).

The service strips any Telegram HTML before synthesizing so the user never
hears angle brackets read out loud.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import edge_tts

logger = logging.getLogger(__name__)

# Voices per language. edge-tts exposes many Neural voices — these are solid
# defaults. Set is intentionally small; callers can override.
_VOICES = {
    "uk": "uk-UA-PolinaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "en": "en-US-JennyNeural",
}

_DEFAULT_LANG = "uk"

# Rough language detection: Cyrillic → uk/ru; Latin → en
_RE_CYR_UK = re.compile(r"[іїєґ]", re.IGNORECASE)  # Ukrainian-only letters
_RE_CYR = re.compile(r"[а-яё]", re.IGNORECASE)
_RE_LAT = re.compile(r"[a-z]", re.IGNORECASE)

_HTML_TAG = re.compile(r"<[^>]+>")
_EMOJI_RANGE = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F02F]"
)


def detect_language(text: str) -> str:
    """Cheap heuristic: Ukrainian-specific letters > generic Cyrillic > Latin."""
    if _RE_CYR_UK.search(text):
        return "uk"
    cyr = len(_RE_CYR.findall(text))
    lat = len(_RE_LAT.findall(text))
    if cyr and cyr >= lat:
        return "ru"
    if lat:
        return "en"
    return _DEFAULT_LANG


def _clean_for_tts(text: str) -> str:
    """Strip HTML, emojis and redundant whitespace — TTS reads cleaner without them."""
    text = _HTML_TAG.sub("", text)
    text = _EMOJI_RANGE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def synthesize(text: str, voice: Optional[str] = None, lang: Optional[str] = None) -> bytes:
    """Render `text` into OGG/Opus audio bytes using edge-tts.

    Returns raw OGG bytes suitable for bot.send_voice().
    Raises RuntimeError on synthesis failure.
    """
    cleaned = _clean_for_tts(text)
    if not cleaned:
        raise ValueError("Nothing to synthesize (text is empty after cleaning)")

    if voice is None:
        lang = lang or detect_language(cleaned)
        voice = _VOICES.get(lang, _VOICES[_DEFAULT_LANG])

    try:
        communicator = edge_tts.Communicate(text=cleaned, voice=voice)
        chunks: list[bytes] = []
        async for message in communicator.stream():
            if message["type"] == "audio":
                chunks.append(message["data"])
        if not chunks:
            raise RuntimeError("edge-tts returned no audio chunks")
        return b"".join(chunks)
    except Exception as e:
        logger.exception("TTS synthesis failed: %s", e)
        raise
