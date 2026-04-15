"""Lightweight aiohttp API for the Telegram Mini App frontend.

Security: validates Telegram WebApp initData via HMAC-SHA256 to ensure
only authenticated Telegram users can access the API.
"""

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qs, unquote

from aiohttp import web

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories import get_or_create_user, get_user_documents, get_user_tags

logger = logging.getLogger(__name__)

# ── Telegram WebApp initData validation ────────────────────────────────────

def _validate_init_data(init_data: str) -> dict | None:
    """Validate Telegram Mini App initData using HMAC-SHA256.

    Returns parsed user dict if valid, None otherwise.
    See: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data:
        return None

    try:
        parsed = parse_qs(init_data, keep_blank_values=True)
        received_hash = parsed.get("hash", [None])[0]
        if not received_hash:
            return None

        # Build data-check-string: sorted key=value pairs, excluding hash
        data_pairs = []
        for key, values in parsed.items():
            if key == "hash":
                continue
            data_pairs.append(f"{key}={values[0]}")
        data_check_string = "\n".join(sorted(data_pairs))

        # HMAC key = HMAC_SHA256(secret_key, "WebAppData")
        secret_key = hmac.new(
            b"WebAppData", settings.bot_token.encode(), hashlib.sha256
        ).digest()
        computed_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            return None

        # Check auth_date is not too old (allow 24h)
        auth_date = int(parsed.get("auth_date", ["0"])[0])
        if time.time() - auth_date > 86400:
            return None

        # Extract user
        user_json = parsed.get("user", [None])[0]
        if user_json:
            return json.loads(unquote(user_json))
    except Exception as e:
        logger.warning("initData validation failed: %s", e)

    return None


def _extract_telegram_id(request: web.Request) -> int | None:
    """Extract and validate telegram_id from request via HMAC-validated initData only."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header:
        user_data = _validate_init_data(auth_header)
        if user_data and "id" in user_data:
            return int(user_data["id"])

    # No fallback — HMAC validation is required
    return None


# ── API handlers ───────────────────────────────────────────────────────────

async def handle_documents(request: web.Request) -> web.Response:
    """GET /api/documents?tag=...&limit=50&offset=0"""
    telegram_id = _extract_telegram_id(request)
    if not telegram_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        tag_filter = request.query.get("tag")
        limit = min(int(request.query.get("limit", "50")), 100)
        offset = max(int(request.query.get("offset", "0")), 0)
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid params"}, status=400)

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=telegram_id)
        docs = await get_user_documents(
            session, user.id, limit=limit, offset=offset, tag_filter=tag_filter
        )

    result = []
    for doc in docs:
        tags = []
        if doc.tags:
            try:
                tags = json.loads(doc.tags)
            except (json.JSONDecodeError, TypeError):
                pass
        result.append(
            {
                "id": doc.id,
                "title": doc.title,
                "source_url": doc.source_url,
                "source_type": doc.source_type,
                "summary": doc.summary,
                "tags": tags,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
            }
        )

    return web.json_response(result)


async def handle_tags(request: web.Request) -> web.Response:
    """GET /api/tags"""
    telegram_id = _extract_telegram_id(request)
    if not telegram_id:
        return web.json_response({"error": "Unauthorized"}, status=401)

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=telegram_id)
        tags = await get_user_tags(session, user.id)

    return web.json_response(tags)


# ── Rate-limiting middleware ───────────────────────────────────────────────

_rate_limits: dict[str, list[float]] = {}
MAX_REQUESTS_PER_MINUTE = 30


def _is_rate_limited(client_id: str) -> bool:
    """Simple in-memory rate limiter."""
    now = time.time()
    window = _rate_limits.setdefault(client_id, [])
    # Remove old entries
    _rate_limits[client_id] = [t for t in window if now - t < 60]
    if len(_rate_limits[client_id]) >= MAX_REQUESTS_PER_MINUTE:
        return True
    _rate_limits[client_id].append(now)
    return False


# ── App factory ────────────────────────────────────────────────────────────

def create_webapp_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/documents", handle_documents)
    app.router.add_get("/api/tags", handle_tags)

    @web.middleware
    async def security_middleware(request, handler):
        # Rate limiting by IP
        client_ip = request.remote or "unknown"
        if _is_rate_limited(client_ip):
            return web.json_response(
                {"error": "Too many requests"}, status=429
            )

        # Handle CORS preflight
        if request.method == "OPTIONS":
            response = web.Response()
        else:
            response = await handler(request)

        # CORS — restrict to webapp_url if configured
        allowed_origin = settings.webapp_url or "*"
        response.headers["Access-Control-Allow-Origin"] = allowed_origin
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"

        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"

        return response

    app.middlewares.append(security_middleware)
    return app
