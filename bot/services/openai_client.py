"""Multi-provider AI client: Groq (free) / OpenAI for LLM & Whisper,
HuggingFace (free) / OpenAI for embeddings."""

import io
import json
import logging
from typing import Optional

import aiohttp
from openai import AsyncOpenAI

from bot.config import settings
from bot.prompts import (
    CHAT_PROMPT,
    QUIZ_PROMPT,
    RAG_USER_TEMPLATE,
    SIMPLIFY_PROMPT,
    SUMMARIZE_USER_TEMPLATE,
    SYSTEM_PROMPT,
    TAG_PROMPT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM client (Groq or OpenAI — both use the OpenAI SDK, different base_url)
# ---------------------------------------------------------------------------

def _build_llm_client() -> AsyncOpenAI:
    if settings.llm_provider == "groq":
        return AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
        )
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )


llm_client = _build_llm_client()

# Groq uses "system" role; OpenAI >=2024 supports "developer"
_SYSTEM_ROLE = "system" if settings.llm_provider == "groq" else "developer"

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

    response = await llm_client.chat.completions.create(
        model=settings.chat_model,
        messages=[
            {"role": _SYSTEM_ROLE, "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": SUMMARIZE_USER_TEMPLATE.format(text=text),
            },
        ],
        temperature=0.3,
        max_tokens=1500,
    )
    return response.choices[0].message.content or ""


async def ask_with_context(question: str, context: str) -> str:
    """Answer a user question using RAG context."""
    response = await llm_client.chat.completions.create(
        model=settings.chat_model,
        messages=[
            {"role": _SYSTEM_ROLE, "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": RAG_USER_TEMPLATE.format(
                    context=context, question=question
                ),
            },
        ],
        temperature=0.4,
        max_tokens=2000,
    )
    return response.choices[0].message.content or ""


async def generate_tags(text: str) -> list[str]:
    """Auto-generate smart tags for a piece of content."""
    snippet = text[:3000]
    response = await llm_client.chat.completions.create(
        model=settings.chat_model,
        messages=[
            {
                "role": "user",
                "content": TAG_PROMPT.format(text=snippet),
            },
        ],
        temperature=0.2,
        max_tokens=200,
    )
    raw = response.choices[0].message.content or "[]"
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
    """Transcribe voice message using Whisper (Groq or OpenAI)."""
    if settings.llm_provider == "groq":
        # Groq offers free Whisper-large-v3-turbo
        whisper_client = AsyncOpenAI(
            api_key=settings.groq_api_key,
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
        language="ru",
    )
    return response.text


# ---------------------------------------------------------------------------
# Quiz generation
# ---------------------------------------------------------------------------

async def generate_quiz(context: str) -> Optional[dict]:
    """Generate a quiz question from knowledge base context."""
    response = await llm_client.chat.completions.create(
        model=settings.chat_model,
        messages=[
            {"role": "user", "content": QUIZ_PROMPT.format(context=context)},
        ],
        temperature=0.7,
        max_tokens=500,
    )
    raw = response.choices[0].message.content or ""
    # Strip markdown code fences if present
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
    response = await llm_client.chat.completions.create(
        model=settings.chat_model,
        messages=[
            {"role": "user", "content": SIMPLIFY_PROMPT.format(text=text)},
        ],
        temperature=0.5,
        max_tokens=1500,
    )
    return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Free chat (no RAG)
# ---------------------------------------------------------------------------

async def free_chat(user_message: str) -> str:
    """Simple chat without RAG context."""
    response = await llm_client.chat.completions.create(
        model=settings.chat_model,
        messages=[
            {"role": _SYSTEM_ROLE, "content": CHAT_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.7,
        max_tokens=2000,
    )
    return response.choices[0].message.content or ""
