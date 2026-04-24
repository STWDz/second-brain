"""RAG pipeline: chunking, embedding, storage, and retrieval."""

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.repositories import (
    add_chunks,
    search_similar_chunks,
    search_similar_chunks_with_docs,
)
from bot.services.openai_client import get_embedding, get_embeddings_batch

logger = logging.getLogger(__name__)


@dataclass
class RagHit:
    """A single retrieved chunk enriched with its parent document metadata."""
    content: str
    document_id: int
    document_title: Optional[str]
    source_url: Optional[str]
    source_type: str


def split_text_into_chunks(
    text: str,
    chunk_size: int = settings.chunk_size,
    overlap: int = settings.chunk_overlap,
) -> list[str]:
    """Split text into overlapping chunks by character count."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start += chunk_size - overlap
    return chunks


async def embed_and_store_chunks(
    session: AsyncSession,
    document_id: int,
    text: str,
) -> int:
    """Split text, embed, and store chunks in the database. Returns chunk count."""
    chunks_text = split_text_into_chunks(text)
    if not chunks_text:
        return 0

    # Batch embed
    embeddings = await get_embeddings_batch(chunks_text)

    chunk_records = [
        {
            "content": chunks_text[i],
            "embedding": embeddings[i],
            "chunk_index": i,
        }
        for i in range(len(chunks_text))
    ]

    await add_chunks(session, document_id, chunk_records)
    return len(chunk_records)


async def retrieve_context(
    session: AsyncSession,
    user_id: int,
    question: str,
    top_k: int = settings.top_k,
) -> str:
    """Retrieve relevant context from the user's knowledge base."""
    query_embedding = await get_embedding(question)
    chunks = await search_similar_chunks(session, user_id, query_embedding, top_k)

    if not chunks:
        return ""

    context_parts: list[str] = []
    for chunk in chunks:
        context_parts.append(chunk.content)

    return "\n\n---\n\n".join(context_parts)


async def retrieve_hits(
    session: AsyncSession,
    user_id: int,
    question: str,
    top_k: int = settings.top_k,
) -> list[RagHit]:
    """Retrieve relevant chunks with their source document metadata."""
    query_embedding = await get_embedding(question)
    rows = await search_similar_chunks_with_docs(
        session, user_id, query_embedding, top_k
    )
    hits: list[RagHit] = []
    for chunk, doc in rows:
        hits.append(
            RagHit(
                content=chunk.content,
                document_id=doc.id,
                document_title=doc.title,
                source_url=doc.source_url,
                source_type=doc.source_type,
            )
        )
    return hits


def format_context_for_prompt(hits: list[RagHit]) -> str:
    """Format RAG hits so the LLM sees a clear source label for each fragment."""
    parts: list[str] = []
    for i, hit in enumerate(hits, 1):
        label = hit.document_title or hit.source_url or f"\u0434\u0436\u0435\u0440\u0435\u043b\u043e #{hit.document_id}"
        parts.append(f"[\u2116{i} | {label}]\n{hit.content}")
    return "\n\n---\n\n".join(parts)


def unique_sources(hits: list[RagHit]) -> list[RagHit]:
    """Collapse hits to one entry per document, preserving order of first appearance."""
    seen: set[int] = set()
    result: list[RagHit] = []
    for hit in hits:
        if hit.document_id in seen:
            continue
        seen.add(hit.document_id)
        result.append(hit)
    return result
