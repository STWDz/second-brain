"""Notion integration: push saved notes into a user's Notion database.

Each user supplies their own integration token and a target database id via
/notion_connect. Tokens are encrypted at rest with Fernet; the key is derived
from BOT_TOKEN so no extra secret is required.

Expected Notion database schema (minimum):
    Name       — title
    Source     — URL (optional)
    Tags       — multi-select (optional)
    Date       — date (optional)
    Type       — select (optional)

If any of these properties don't exist in the target DB, Notion silently
ignores them; the row is still created.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import aiohttp
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import Document, NotionIntegration

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")


# ── Token crypto ───────────────────────────────────────────────────────────


def _fernet() -> Fernet:
    """Derive a stable 32-byte key from BOT_TOKEN via SHA-256."""
    digest = hashlib.sha256(settings.bot_token.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str) -> Optional[str]:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.warning("Failed to decrypt Notion token (bot token changed?)")
        return None


# ── Repository helpers ────────────────────────────────────────────────────


async def get_integration(
    session: AsyncSession, user_id: int
) -> Optional[NotionIntegration]:
    result = await session.execute(
        select(NotionIntegration).where(NotionIntegration.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def upsert_integration(
    session: AsyncSession,
    user_id: int,
    token: str,
    database_id: str,
) -> NotionIntegration:
    existing = await get_integration(session, user_id)
    if existing is None:
        existing = NotionIntegration(
            user_id=user_id,
            token_encrypted=encrypt_token(token),
            database_id=normalize_database_id(database_id),
        )
        session.add(existing)
    else:
        existing.token_encrypted = encrypt_token(token)
        existing.database_id = normalize_database_id(database_id)
    await session.flush()
    return existing


async def delete_integration(session: AsyncSession, user_id: int) -> bool:
    integration = await get_integration(session, user_id)
    if integration is None:
        return False
    await session.delete(integration)
    return True


def normalize_database_id(raw: str) -> str:
    """Extract UUID from a Notion URL or raw string; return with dashes."""
    raw = raw.strip()
    match = _UUID_RE.search(raw)
    if not match:
        raise ValueError("Не схоже на Notion database ID.")
    hex_only = match.group(0).replace("-", "").lower()
    return (
        f"{hex_only[0:8]}-{hex_only[8:12]}-{hex_only[12:16]}-"
        f"{hex_only[16:20]}-{hex_only[20:32]}"
    )


# ── Notion API ────────────────────────────────────────────────────────────


@dataclass
class NotionError(Exception):
    status: int
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"Notion API {self.status}: {self.message}"


async def _notion_request(
    token: str, method: str, path: str, payload: Optional[dict] = None
) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    url = f"{NOTION_API}{path}"
    async with aiohttp.ClientSession() as session:
        async with session.request(
            method, url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            body = await resp.text()
            if resp.status >= 400:
                try:
                    data = json.loads(body)
                    msg = data.get("message", body)
                except ValueError:
                    msg = body
                raise NotionError(status=resp.status, message=msg)
            return json.loads(body) if body else {}


async def verify_credentials(token: str, database_id: str) -> str:
    """Return the database title if credentials are valid, raise otherwise."""
    db = await _notion_request(token, "GET", f"/databases/{database_id}")
    title_arr = db.get("title") or []
    return "".join(part.get("plain_text", "") for part in title_arr) or "(без назви)"


# Notion content blocks have a hard limit of 2000 chars each
_BLOCK_CHAR_LIMIT = 1900


def _split_for_blocks(text: str) -> list[str]:
    """Split long text into paragraph-friendly chunks under Notion's 2000-char limit."""
    if not text:
        return []
    parts: list[str] = []
    remaining = text
    while len(remaining) > _BLOCK_CHAR_LIMIT:
        cut = remaining.rfind("\n", 0, _BLOCK_CHAR_LIMIT)
        if cut == -1:
            cut = _BLOCK_CHAR_LIMIT
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


def _rich_text(content: str) -> list[dict]:
    return [{"type": "text", "text": {"content": content}}]


def _document_to_page_payload(doc: Document, database_id: str) -> dict:
    """Map a Document to a Notion pages.create request body."""
    props: dict = {
        "Name": {
            "title": _rich_text(doc.title or "(без назви)"),
        }
    }
    if doc.source_url:
        props["Source"] = {"url": doc.source_url}
    if doc.source_type:
        props["Type"] = {"select": {"name": doc.source_type}}
    if doc.tags:
        try:
            tag_list = json.loads(doc.tags)
            if isinstance(tag_list, list):
                # Notion multi-select names cannot contain commas
                props["Tags"] = {
                    "multi_select": [
                        {"name": str(t).lstrip("#").replace(",", " ")[:100]}
                        for t in tag_list[:10]
                        if t
                    ]
                }
        except (ValueError, TypeError):
            pass
    if doc.created_at:
        props["Date"] = {"date": {"start": doc.created_at.date().isoformat()}}

    children = []
    if doc.summary:
        for piece in _split_for_blocks(doc.summary):
            children.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": _rich_text(piece)},
                }
            )

    return {
        "parent": {"database_id": database_id},
        "properties": props,
        "children": children,
    }


async def push_document(token: str, database_id: str, doc: Document) -> str:
    """Create a page in Notion for `doc`. Returns the new page id."""
    payload = _document_to_page_payload(doc, database_id)
    data = await _notion_request(token, "POST", "/pages", payload)
    return data.get("id", "")


async def push_documents(
    token: str, database_id: str, docs: list[Document]
) -> tuple[int, int]:
    """Push each document as a separate page. Returns (success, failures)."""
    success = 0
    failed = 0
    for doc in docs:
        try:
            await push_document(token, database_id, doc)
            success += 1
            # Be polite to Notion API (rate limit ~3 req/s)
            await asyncio.sleep(0.4)
        except NotionError as e:
            logger.warning("Notion push failed for doc %s: %s", doc.id, e)
            failed += 1
        except Exception as e:
            logger.exception("Unexpected Notion error for doc %s: %s", doc.id, e)
            failed += 1
    return success, failed
