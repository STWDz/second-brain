"""Maximum security middlewares for the Telegram bot.

Layers (applied in order):
1. PrivateOnlyMiddleware — block groups/channels
2. WhitelistMiddleware   — only allowed user IDs (if configured)
3. AntiSpamMiddleware    — detect repeated identical messages
4. RateLimitMiddleware   — throttle per user (messages + callbacks)
5. FileSizeMiddleware    — reject oversized uploads
6. InputSanitizeMiddleware — trim text, limit length
7. AuditLogMiddleware    — log all incoming events for security review
"""

import hashlib
import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import settings

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("audit")


# ── 1. Private-only ───────────────────────────────────────────────────────

class PrivateOnlyMiddleware(BaseMiddleware):
    """Block non-private chats (groups, supergroups, channels)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not settings.private_only:
            return await handler(event, data)

        if isinstance(event, Message) and event.chat.type != "private":
            logger.warning(
                "Blocked non-private chat: type=%s chat_id=%s user=%s",
                event.chat.type, event.chat.id,
                event.from_user.id if event.from_user else "?",
            )
            await event.answer("🔒 Бот працює тільки в особистих повідомленнях.")
            return None

        return await handler(event, data)


# ── 2. Whitelist ──────────────────────────────────────────────────────────

class WhitelistMiddleware(BaseMiddleware):
    """Only allow configured Telegram user IDs. Empty whitelist = all allowed."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        allowed = settings.allowed_user_ids
        if not allowed:
            return await handler(event, data)

        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        if user_id and user_id not in allowed:
            logger.warning("Blocked unauthorized user: %s", user_id)
            if isinstance(event, Message):
                await event.answer("🔒 Доступ обмежено. Звернись до адміністратора.")
            elif isinstance(event, CallbackQuery):
                await event.answer("🔒 Доступ обмежено.", show_alert=True)
            return None

        return await handler(event, data)


# ── 3. Anti-spam (detect repeated identical messages) ─────────────────────

class AntiSpamMiddleware(BaseMiddleware):
    """Block users who send the same message repeatedly.

    Uses a stable SHA-1 of the text so comparisons survive process restarts
    and are resistant to Python's hash randomization. State is GC'd after
    each user's window expires to bound memory under a flood of unique IDs.
    """

    _MAX_TRACKED_USERS = 10_000  # hard cap against unbounded growth

    def __init__(self, max_repeats: int = 3, window_seconds: int = 30) -> None:
        self.max_repeats = max_repeats
        self.window = window_seconds
        self._recent: dict[int, list[tuple[float, str]]] = {}
        self._last_gc: float = 0.0

    def _gc(self, now: float) -> None:
        """Drop entries that are entirely outside the current window."""
        if now - self._last_gc < self.window:
            return
        self._last_gc = now
        cutoff = now - self.window
        dead = [uid for uid, entries in self._recent.items()
                if not entries or entries[-1][0] < cutoff]
        for uid in dead:
            self._recent.pop(uid, None)
        # Hard cap: if still huge (e.g. unique attacker IDs), evict oldest.
        if len(self._recent) > self._MAX_TRACKED_USERS:
            victims = sorted(self._recent.items(), key=lambda kv: kv[1][-1][0] if kv[1] else 0)
            for uid, _ in victims[: len(self._recent) - self._MAX_TRACKED_USERS]:
                self._recent.pop(uid, None)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.from_user:
            return await handler(event, data)

        uid = event.from_user.id
        now = time.time()
        self._gc(now)

        # Stable fingerprint — does not depend on Python's per-process salt.
        fp_src = (event.text or "") + "|" + (
            (event.voice.file_unique_id if event.voice else "")
            or (event.video_note.file_unique_id if event.video_note else "")
            or (event.document.file_unique_id if event.document else "")
            or ""
        )
        content_hash = hashlib.sha1(fp_src.encode("utf-8")).hexdigest()

        entries = self._recent.setdefault(uid, [])
        self._recent[uid] = [(t, h) for t, h in entries if now - t < self.window]

        same_count = sum(1 for _, h in self._recent[uid] if h == content_hash)
        if same_count >= self.max_repeats:
            logger.warning("Anti-spam triggered for user %s", uid)
            await event.answer("🚫 Виявлено спам. Зачекай перед повтором.")
            return None

        self._recent[uid].append((now, content_hash))
        return await handler(event, data)


# ── 4. Rate Limiter (messages + callbacks) ─────────────────────────────────

class RateLimitMiddleware(BaseMiddleware):
    """Limits events per user to prevent abuse.

    State is GC'd every `window` seconds so the dict can't grow unbounded when
    many unique user IDs arrive (e.g. during an attack). A hard cap evicts the
    oldest entries if the GC can't keep up.
    """

    _MAX_TRACKED_USERS = 10_000

    def __init__(self, max_events: int = 15, window_seconds: int = 60) -> None:
        self.max_events = max_events
        self.window = window_seconds
        self._user_timestamps: dict[int, list[float]] = {}
        self._last_gc: float = 0.0

    def _gc(self, now: float) -> None:
        if now - self._last_gc < self.window:
            return
        self._last_gc = now
        cutoff = now - self.window
        dead = [uid for uid, ts in self._user_timestamps.items()
                if not ts or ts[-1] < cutoff]
        for uid in dead:
            self._user_timestamps.pop(uid, None)
        if len(self._user_timestamps) > self._MAX_TRACKED_USERS:
            victims = sorted(
                self._user_timestamps.items(),
                key=lambda kv: kv[1][-1] if kv[1] else 0,
            )
            for uid, _ in victims[: len(self._user_timestamps) - self._MAX_TRACKED_USERS]:
                self._user_timestamps.pop(uid, None)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        if not user_id:
            return await handler(event, data)

        now = time.time()
        self._gc(now)
        timestamps = self._user_timestamps.setdefault(user_id, [])
        self._user_timestamps[user_id] = [t for t in timestamps if now - t < self.window]

        if len(self._user_timestamps[user_id]) >= self.max_events:
            logger.warning("Rate limit hit for user %s", user_id)
            if isinstance(event, Message):
                await event.answer(
                    "⚠️ Забагато запитів. Зачекай хвилинку."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("⚠️ Занадто швидко! Зачекай.", show_alert=True)
            return None

        self._user_timestamps[user_id].append(now)
        return await handler(event, data)


# ── 5. File size guard ─────────────────────────────────────────────────────

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


class FileSizeMiddleware(BaseMiddleware):
    """Rejects files larger than MAX_FILE_SIZE."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            file_size = None
            if event.document:
                file_size = event.document.file_size
            elif event.voice:
                file_size = event.voice.file_size
            elif event.video_note:
                file_size = event.video_note.file_size
            elif event.audio:
                file_size = event.audio.file_size
            elif event.video:
                file_size = event.video.file_size

            if file_size and file_size > MAX_FILE_SIZE:
                await event.answer(
                    f"⚠️ Файл занадто великий ({file_size // (1024*1024)} МБ). "
                    f"Максимум: {MAX_FILE_SIZE // (1024*1024)} МБ."
                )
                return None

        return await handler(event, data)


# ── 6. Input sanitization ─────────────────────────────────────────────────

class InputSanitizeMiddleware(BaseMiddleware):
    """Trim and limit text-like input length (plain text + photo captions)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            value = event.text or event.caption
            if value and len(value) > settings.max_text_length:
                await event.answer(
                    f"⚠️ Текст занадто довгий ({len(value)} символів). "
                    f"Максимум: {settings.max_text_length}."
                )
                return None

        return await handler(event, data)


# ── 7. Audit log ──────────────────────────────────────────────────────────

class AuditLogMiddleware(BaseMiddleware):
    """Log all incoming events for security monitoring."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user:
            content_type = event.content_type.value if event.content_type else "unknown"
            preview = ""
            if event.text:
                preview = event.text[:80].replace("\n", " ")
            audit_logger.info(
                "MSG user=%s @%s chat=%s type=%s preview='%s'",
                event.from_user.id,
                event.from_user.username or "?",
                event.chat.id,
                content_type,
                preview,
            )
        elif isinstance(event, CallbackQuery) and event.from_user:
            audit_logger.info(
                "CALLBACK user=%s @%s data='%s'",
                event.from_user.id,
                event.from_user.username or "?",
                event.data or "?",
            )

        return await handler(event, data)
