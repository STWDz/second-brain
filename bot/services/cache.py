"""Async cache abstraction.

Uses Redis when REDIS_URL is configured, falls back to an in-process
LRU + TTL dict otherwise. All operations are no-ops on errors so a Redis
outage never takes the bot down.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any, Optional

from bot.config import settings

logger = logging.getLogger(__name__)

_redis: Any = None
_redis_init_lock = asyncio.Lock()
_redis_ready = False


async def _get_redis() -> Any:
    """Lazy, single-attempt connect. On failure, disable Redis for this run."""
    global _redis, _redis_ready
    if _redis_ready:
        return _redis
    async with _redis_init_lock:
        if _redis_ready:
            return _redis
        if not settings.redis_url:
            _redis_ready = True
            return None
        try:
            import redis.asyncio as redis_async  # type: ignore

            client = redis_async.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=False,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
            # Ping to confirm reachability — otherwise fall back silently
            await client.ping()
            _redis = client
            logger.info("Redis cache enabled (%s)", _mask_url(settings.redis_url))
        except Exception as e:
            logger.warning("Redis unavailable, using in-memory cache: %s", e)
            _redis = None
        _redis_ready = True
        return _redis


def _mask_url(url: str) -> str:
    """Hide credentials for safe logging."""
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    _, host = rest.split("@", 1)
    return f"{scheme}://***@{host}"


# ── In-memory fallback ──────────────────────────────────────────────────────

_MEM_MAX = 512
_mem: OrderedDict[str, tuple[float, bytes]] = OrderedDict()


def _mem_get(key: str) -> Optional[bytes]:
    entry = _mem.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if expires_at and expires_at < time.time():
        _mem.pop(key, None)
        return None
    _mem.move_to_end(key)
    return value


def _mem_set(key: str, value: bytes, ttl: int) -> None:
    expires_at = time.time() + ttl if ttl else 0.0
    _mem[key] = (expires_at, value)
    _mem.move_to_end(key)
    while len(_mem) > _MEM_MAX:
        _mem.popitem(last=False)


# ── Public API ──────────────────────────────────────────────────────────────

async def cache_get_bytes(key: str) -> Optional[bytes]:
    client = await _get_redis()
    if client is not None:
        try:
            return await client.get(key)
        except Exception as e:
            logger.warning("Redis get failed (%s), using memory: %s", key[:20], e)
    return _mem_get(key)


async def cache_set_bytes(key: str, value: bytes, ttl: int) -> None:
    client = await _get_redis()
    if client is not None:
        try:
            await client.set(key, value, ex=ttl if ttl else None)
            return
        except Exception as e:
            logger.warning("Redis set failed (%s), using memory: %s", key[:20], e)
    _mem_set(key, value, ttl)


async def cache_get_json(key: str) -> Optional[Any]:
    raw = await cache_get_bytes(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


async def cache_set_json(key: str, value: Any, ttl: int) -> None:
    payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
    await cache_set_bytes(key, payload, ttl)


def text_key(prefix: str, *parts: str) -> str:
    """Stable cache key: prefix:sha256(joined parts)."""
    h = hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}:{h}"
