"""RAG pipeline: chunking, embedding, storage, and retrieval."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.repositories import add_chunks, search_similar_chunks
from bot.services.openai_client import get_embedding, get_embeddings_batch

logger = logging.getLogger(__name__)


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
