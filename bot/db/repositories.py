import json
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Chunk, Document, User


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
) -> User:
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            telegram_id=telegram_id, username=username, first_name=first_name
        )
        session.add(user)
        await session.flush()
    return user


async def create_document(
    session: AsyncSession,
    user_id: int,
    title: Optional[str],
    source_url: Optional[str],
    source_type: str,
    summary: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> Document:
    doc = Document(
        user_id=user_id,
        title=title,
        source_url=source_url,
        source_type=source_type,
        summary=summary,
        tags=json.dumps(tags, ensure_ascii=False) if tags else None,
    )
    session.add(doc)
    await session.flush()
    return doc


async def add_chunks(
    session: AsyncSession,
    document_id: int,
    chunks: list[dict],
) -> None:
    """chunks: list of {"content": str, "embedding": list[float], "chunk_index": int}"""
    objects = [
        Chunk(
            document_id=document_id,
            content=c["content"],
            embedding=c["embedding"],
            chunk_index=c["chunk_index"],
        )
        for c in chunks
    ]
    session.add_all(objects)
    await session.flush()


async def search_similar_chunks(
    session: AsyncSession,
    user_id: int,
    query_embedding: list[float],
    top_k: int = 5,
) -> Sequence[Chunk]:
    embedding_literal = str(query_embedding)
    stmt = (
        select(Chunk)
        .join(Document, Chunk.document_id == Document.id)
        .where(Document.user_id == user_id)
        .order_by(Chunk.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def search_similar_chunks_with_docs(
    session: AsyncSession,
    user_id: int,
    query_embedding: list[float],
    top_k: int = 5,
) -> Sequence[tuple[Chunk, Document]]:
    """Like search_similar_chunks, but also returns the parent Document."""
    stmt = (
        select(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .where(Document.user_id == user_id)
        .order_by(Chunk.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )
    result = await session.execute(stmt)
    return result.all()


async def get_user_documents(
    session: AsyncSession,
    user_id: int,
    limit: int = 50,
    offset: int = 0,
    tag_filter: Optional[str] = None,
) -> Sequence[Document]:
    stmt = (
        select(Document)
        .where(Document.user_id == user_id)
        .order_by(Document.created_at.desc())
    )
    if tag_filter:
        stmt = stmt.where(Document.tags.contains(tag_filter))
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_random_old_document(
    session: AsyncSession,
    user_id: int,
    days_ago: int = 30,
) -> Optional[Document]:
    # Parameterize interval via make_interval() to avoid string interpolation in SQL
    days = max(1, int(days_ago))
    result = await session.execute(
        select(Document)
        .where(Document.user_id == user_id)
        .where(
            Document.created_at
            <= func.now() - func.make_interval(0, 0, 0, days)
        )
        .order_by(func.random())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_random_document(
    session: AsyncSession,
    user_id: int,
) -> Optional[Document]:
    """Get any random document from user's collection."""
    result = await session.execute(
        select(Document)
        .where(Document.user_id == user_id)
        .order_by(func.random())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def delete_document(
    session: AsyncSession, document_id: int, user_id: Optional[int] = None
) -> bool:
    """Delete a document (and its chunks).

    If `user_id` is provided we scope the delete to that owner so a caller
    can't accidentally remove someone else's data even if it passes the wrong
    document_id. Returns True if a row was deleted.
    """
    from sqlalchemy import delete as sa_delete

    stmt = sa_delete(Document).where(Document.id == document_id)
    if user_id is not None:
        stmt = stmt.where(Document.user_id == user_id)
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount > 0


async def get_document_by_id(
    session: AsyncSession,
    document_id: int,
    user_id: Optional[int] = None,
) -> Optional[Document]:
    """Return the document only if it belongs to `user_id` (when provided)."""
    stmt = select(Document).where(Document.id == document_id)
    if user_id is not None:
        stmt = stmt.where(Document.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_stats(session: AsyncSession, user_id: int) -> dict:
    """Get user statistics."""
    from sqlalchemy import func as sa_func

    # Total docs
    total_result = await session.execute(
        select(sa_func.count(Document.id)).where(Document.user_id == user_id)
    )
    total = total_result.scalar() or 0

    # By type
    type_result = await session.execute(
        select(Document.source_type, sa_func.count(Document.id))
        .where(Document.user_id == user_id)
        .group_by(Document.source_type)
    )
    by_type = {row[0]: row[1] for row in type_result}

    # Total chunks
    chunk_result = await session.execute(
        select(sa_func.count(Chunk.id))
        .join(Document, Chunk.document_id == Document.id)
        .where(Document.user_id == user_id)
    )
    total_chunks = chunk_result.scalar() or 0

    # Top tags
    tags = await get_user_tags(session, user_id)

    # First and last save dates
    dates_result = await session.execute(
        select(
            sa_func.min(Document.created_at),
            sa_func.max(Document.created_at),
        ).where(Document.user_id == user_id)
    )
    dates = dates_result.one_or_none()
    first_save = dates[0] if dates else None
    last_save = dates[1] if dates else None

    return {
        "total": total,
        "by_type": by_type,
        "total_chunks": total_chunks,
        "tags_count": len(tags),
        "top_tags": tags[:10],
        "first_save": first_save,
        "last_save": last_save,
    }


async def search_documents_text(
    session: AsyncSession,
    user_id: int,
    query: str,
    limit: int = 10,
) -> Sequence[Document]:
    """Full-text search across titles, summaries, and tags.

    SQL LIKE wildcards (%, _, \\) from the user are escaped so they can't be
    used to match everything.
    """
    safe = (
        query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    pattern = f"%{safe}%"
    stmt = (
        select(Document)
        .where(Document.user_id == user_id)
        .where(
            (Document.title.ilike(pattern, escape="\\"))
            | (Document.summary.ilike(pattern, escape="\\"))
            | (Document.tags.ilike(pattern, escape="\\"))
        )
        .order_by(Document.created_at.desc())
        .limit(max(1, min(int(limit), 100)))
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def toggle_pin(
    session: AsyncSession, document_id: int, user_id: Optional[int] = None
) -> bool:
    """Toggle pin status (scoped to `user_id` if given). Returns new is_pinned."""
    doc = await get_document_by_id(session, document_id, user_id=user_id)
    if not doc:
        return False
    doc.is_pinned = not doc.is_pinned
    await session.flush()
    return doc.is_pinned


async def get_pinned_documents(
    session: AsyncSession,
    user_id: int,
) -> Sequence[Document]:
    stmt = (
        select(Document)
        .where(Document.user_id == user_id)
        .where(Document.is_pinned == True)
        .order_by(Document.created_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_recent_documents(
    session: AsyncSession,
    user_id: int,
    days: int = 7,
    limit: int = 20,
) -> Sequence[Document]:
    """Get documents from the last N days."""
    days = max(1, int(days))
    limit = max(1, min(int(limit), 200))
    stmt = (
        select(Document)
        .where(Document.user_id == user_id)
        .where(
            Document.created_at
            >= func.now() - func.make_interval(0, 0, 0, days)
        )
        .order_by(Document.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_user_tags(session: AsyncSession, user_id: int) -> list[str]:
    stmt = (
        select(Document.tags)
        .where(Document.user_id == user_id)
        .where(Document.tags.isnot(None))
    )
    result = await session.execute(stmt)
    all_tags: set[str] = set()
    for (tags_json,) in result:
        try:
            tags = json.loads(tags_json)
            all_tags.update(tags)
        except (json.JSONDecodeError, TypeError):
            pass
    return sorted(all_tags)


# ── Global / admin-only queries ────────────────────────────────────────────


async def get_global_stats(session: AsyncSession) -> dict:
    """Aggregate counts across all users — for admin /admin_stats."""
    total_users = (await session.execute(select(func.count(User.id)))).scalar_one()
    total_docs = (await session.execute(select(func.count(Document.id)))).scalar_one()
    total_chunks = (await session.execute(select(func.count(Chunk.id)))).scalar_one()
    by_type_rows = await session.execute(
        select(Document.source_type, func.count(Document.id)).group_by(
            Document.source_type
        )
    )
    by_type = {row[0]: row[1] for row in by_type_rows}
    active_7d = (
        await session.execute(
            select(func.count(func.distinct(Document.user_id))).where(
                Document.created_at
                >= func.now() - func.make_interval(0, 0, 0, 7)
            )
        )
    ).scalar_one()
    return {
        "users": total_users,
        "documents": total_docs,
        "chunks": total_chunks,
        "by_type": by_type,
        "active_7d": active_7d,
    }


async def list_recent_users(
    session: AsyncSession, limit: int = 20
) -> Sequence[User]:
    stmt = select(User).order_by(User.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


async def list_all_telegram_ids(session: AsyncSession) -> list[int]:
    """Telegram IDs of every registered user — used for admin broadcasts."""
    result = await session.execute(select(User.telegram_id))
    return [row[0] for row in result]
