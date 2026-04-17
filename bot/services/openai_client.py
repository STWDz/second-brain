"""Multi-provider AI client: Groq (free) / OpenAI for LLM & Whisper,
HuggingFace (free) / OpenAI for embeddings.

Features:
- Key rotation: multiple Groq keys cycled round-robin
- Response cache: LRU with TTL to avoid duplicate API calls
- Retry with backoff: auto-retry on 429 rate limit errors
"""

import asyncio
import hashlib
import io
import json
import logging
import time
from collections import OrderedDict
from itertools import cycle
from typing import Optional

import aiohttp
from openai import AsyncOpenAI, RateLimitError

from bot.config import settings
from bot.prompts import (
    CHAT_PROMPT,
    CONSPECT_PROMPT,
    QUIZ_PROMPT,
    RAG_USER_TEMPLATE,
    SIMPLIFY_PROMPT,
    SUMMARIZE_USER_TEMPLATE,
    SYSTEM_PROMPT,
    TAG_PROMPT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Key rotation: cycle through multiple Groq API keys
# ---------------------------------------------------------------------------

_groq_keys = settings.groq_api_keys or [settings.groq_api_key]
_key_cycle = cycle(_groq_keys)
logger.info("Groq key rotation: %d key(s) loaded", len(_groq_keys))


def _next_groq_key() -> str:
    """Get the next Groq API key in round-robin."""
    return next(_key_cycle)


def _build_llm_client() -> AsyncOpenAI:
    if settings.llm_provider == "groq":
        return AsyncOpenAI(
            api_key=_next_groq_key(),
            base_url=settings.groq_base_url,
        )
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )


def _rotate_llm_client() -> AsyncOpenAI:
    """Build a new client with the next rotated key."""
    if settings.llm_provider == "groq":
        return AsyncOpenAI(
            api_key=_next_groq_key(),
            base_url=settings.groq_base_url,
        )
    return _build_llm_client()


llm_client = _build_llm_client()

_SYSTEM_ROLE = "system" if settings.llm_provider == "groq" else "developer"

# ---------------------------------------------------------------------------
# Response cache: LRU with TTL (avoids duplicate API calls)
# ---------------------------------------------------------------------------

_CACHE_MAX = 128
_CACHE_TTL = 600  # 10 minutes
_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()


def _cache_key(model: str, messages: list[dict], temperature: float) -> str:
    raw = json.dumps({"m": model, "msgs": messages, "t": temperature}, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(key: str) -> Optional[str]:
    if key in _cache:
        ts, val = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            _cache.move_to_end(key)
            return val
        del _cache[key]
    return None


def _cache_set(key: str, value: str) -> None:
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


# ---------------------------------------------------------------------------
# Retry wrapper with key rotation on 429
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0  # seconds


async def _chat_completion(
    messages: list[dict],
    temperature: float = 0.4,
    max_tokens: int = 2000,
    use_cache: bool = True,
) -> str:
    """Unified chat completion with cache, retry, and key rotation."""
    global llm_client
    model = settings.chat_model

    if use_cache:
        ck = _cache_key(model, messages, temperature)
        cached = _cache_get(ck)
        if cached is not None:
            logger.debug("Cache hit for %s", ck[:8])
            return cached

    last_error = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await llm_client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            result = response.choices[0].message.content or ""
            if use_cache:
                _cache_set(ck, result)
            return result
        except RateLimitError as e:
            last_error = e
            delay = _RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "Rate limit (attempt %d/%d), rotating key, retry in %.1fs",
                attempt + 1, _MAX_RETRIES, delay,
            )
            llm_client = _rotate_llm_client()
            await asyncio.sleep(delay)
        except Exception as e:
            logger.exception("LLM call failed: %s", e)
            raise

    raise last_error or RuntimeError("All retries exhausted")

# ---------------------------------------------------------------------------
# Embeddings via HuggingFace Inference API (free) or OpenAI
# ---------------------------------------------------------------------------

HF_INFERENCE_URL = (
    f"https://router.huggingface.co/hf-inference/models/"
    f"{settings.hf_embedding_model}"
)


async def _hf_embed(texts: list[str]) -> list[list[float]]:
    """Get embeddings from HuggingFace free Inference API."""
    headers = {}
    if settings.hf_api_key:
        headers["Authorization"] = f"Bearer {settings.hf_api_key}"
    payload = {"inputs": texts, "options": {"wait_for_model": True}}

    async with aiohttp.ClientSession() as session:
        async with session.post(HF_INFERENCE_URL, json=payload, headers=headers) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"HF Inference API error {resp.status}: {body}")
            data = await resp.json()
            return data  # list[list[float]]


async def _openai_embed(texts: list[str]) -> list[list[float]]:
    """Get embeddings from OpenAI API."""
    openai_client = AsyncOpenAI(
        api_key=settings.openai_api_key, base_url=settings.openai_base_url
    )
    response = await openai_client.embeddings.create(
        input=texts, model=settings.openai_embedding_model
    )
    return [item.embedding for item in response.data]


async def get_embedding(text: str) -> list[float]:
    """Get embedding vector for a single text."""
    text = text.replace("\n", " ").strip()
    results = await get_embeddings_batch([text])
    return results[0]


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Get embeddings for a batch of texts using the configured provider."""
    cleaned = [t.replace("\n", " ").strip() for t in texts]
    if settings.embedding_provider == "openai":
        return await _openai_embed(cleaned)
    return await _hf_embed(cleaned)


# ---------------------------------------------------------------------------
# Chat completions (works with both Groq and OpenAI)
# ---------------------------------------------------------------------------

async def summarize_text(text: str) -> str:
    """Summarize text using the system prompt style."""
    if len(text) > 15000:
        text = text[:15000] + "..."
    return await _chat_completion(
        messages=[
            {"role": _SYSTEM_ROLE, "content": SYSTEM_PROMPT},
            {"role": "user", "content": SUMMARIZE_USER_TEMPLATE.format(text=text)},
        ],
        temperature=0.3,
        max_tokens=1500,
    )


async def ask_with_context(question: str, context: str) -> str:
    """Answer a user question using RAG context."""
    return await _chat_completion(
        messages=[
            {"role": _SYSTEM_ROLE, "content": SYSTEM_PROMPT},
            {"role": "user", "content": RAG_USER_TEMPLATE.format(context=context, question=question)},
        ],
        temperature=0.4,
        max_tokens=2000,
    )


async def generate_tags(text: str) -> list[str]:
    """Auto-generate smart tags for a piece of content."""
    snippet = text[:3000]
    raw = await _chat_completion(
        messages=[{"role": "user", "content": TAG_PROMPT.format(text=snippet)}],
        temperature=0.2,
        max_tokens=200,
    )
    try:
        tags = json.loads(raw)
        if isinstance(tags, list):
            return [str(t) for t in tags[:5]]
    except json.JSONDecodeError:
        logger.warning("Failed to parse tags: %s", raw)
    return []


# ---------------------------------------------------------------------------
# Voice transcription — Groq Whisper (free) or OpenAI Whisper
# ---------------------------------------------------------------------------

async def transcribe_voice(file_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Transcribe voice message using Whisper (Groq or OpenAI) with retry."""
    global llm_client
    for attempt in range(_MAX_RETRIES):
        try:
            if settings.llm_provider == "groq":
                whisper_client = AsyncOpenAI(
                    api_key=_next_groq_key(),
                    base_url=settings.groq_base_url,
                )
                model = "whisper-large-v3-turbo"
            else:
                whisper_client = AsyncOpenAI(
                    api_key=settings.openai_api_key,
                    base_url=settings.openai_base_url,
                )
                model = "whisper-1"

            audio_file = io.BytesIO(file_bytes)
            audio_file.name = filename
            response = await whisper_client.audio.transcriptions.create(
                model=model,
                file=audio_file,
                prompt="Розмова українською та російською мовами. Розпізнай чітко кожне слово.",
            )
            return response.text
        except RateLimitError:
            delay = _RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning("Whisper rate limit (attempt %d/%d), retry in %.1fs", attempt + 1, _MAX_RETRIES, delay)
            await asyncio.sleep(delay)
    raise RuntimeError("Whisper: all retries exhausted")


# ---------------------------------------------------------------------------
# Quiz generation
# ---------------------------------------------------------------------------

async def generate_quiz(context: str) -> Optional[dict]:
    """Generate a quiz question from knowledge base context."""
    raw = await _chat_completion(
        messages=[{"role": "user", "content": QUIZ_PROMPT.format(context=context)}],
        temperature=0.7,
        max_tokens=500,
        use_cache=False,
    )
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()
    try:
        data = json.loads(raw)
        if "question" in data and "options" in data and "correct" in data:
            return data
    except json.JSONDecodeError:
        logger.warning("Failed to parse quiz JSON: %s", raw)
    return None


# ---------------------------------------------------------------------------
# Simplify text
# ---------------------------------------------------------------------------

async def simplify_text(text: str) -> str:
    """Re-explain text in very simple terms."""
    if len(text) > 10000:
        text = text[:10000] + "..."
    return await _chat_completion(
        messages=[{"role": "user", "content": SIMPLIFY_PROMPT.format(text=text)}],
        temperature=0.5,
        max_tokens=1500,
    )


# ---------------------------------------------------------------------------
# Free chat (no RAG)
# ---------------------------------------------------------------------------

async def free_chat(user_message: str) -> str:
    """Simple chat without RAG context."""
    return await _chat_completion(
        messages=[
            {"role": _SYSTEM_ROLE, "content": CHAT_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.7,
        max_tokens=2000,
        use_cache=False,
    )


# ---------------------------------------------------------------------------
# Conspect generation
# ---------------------------------------------------------------------------

async def make_conspect(text: str) -> str:
    """Generate a structured conspect from text."""
    if len(text) > 15000:
        text = text[:15000] + "..."
    return await _chat_completion(
        messages=[
            {"role": _SYSTEM_ROLE, "content": SYSTEM_PROMPT},
            {"role": "user", "content": CONSPECT_PROMPT.format(text=text)},
        ],
        temperature=0.3,
        max_tokens=3000,
    )
