"""SSRF-hardened HTTP fetcher.

Why a dedicated module:
    trafilatura.fetch_url() does its own DNS resolution *after* we check the
    hostname, which is vulnerable to DNS rebinding attacks (attacker's domain
    resolves to a public IP the first time, then to 127.0.0.1 on the fetch).
    This fetcher does TWO things:

    1. Pre-validates the hostname and every redirect target.
    2. Installs a custom aiohttp resolver that re-validates every DNS answer
       during the actual connect — so even if an attacker rebinds their name
       between our pre-check and aiohttp's connect, the connection still fails.

Protections:
    * schemes limited to http/https
    * blocks loopback, private, link-local, multicast, reserved, unspecified,
      broadcast and IPv4-mapped-IPv6 addresses
    * follows up to MAX_REDIRECTS redirects, re-validating every hop
    * hard cap on response body size (streaming read) + wall-clock timeout
    * uses aiohttp so the event loop stays free
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urlunparse

import aiohttp
from aiohttp.resolver import DefaultResolver

logger = logging.getLogger(__name__)

MAX_URL_LENGTH = 2048
MAX_BYTES = 8 * 1024 * 1024  # 8 MB per page — more than enough for any article
MAX_REDIRECTS = 5
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=5, sock_read=10)
USER_AGENT = (
    "Mozilla/5.0 (compatible; Cortex-Bot/1.0; +https://github.com/STWDZ/second-brain)"
)


@dataclass
class FetchResult:
    ok: bool
    body: Optional[bytes] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    final_url: Optional[str] = None


def _is_ip_public(ip: ipaddress._BaseAddress) -> bool:
    """Reject every IP that should never be contacted from a public service."""
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_reserved
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return False
    # IPv4: also block directed broadcast.
    if isinstance(ip, ipaddress.IPv4Address) and int(ip) == 0xFFFFFFFF:
        return False
    # Block IPv4-mapped IPv6 ::ffff:127.0.0.1 etc.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_ip_public(ip.ipv4_mapped)
    return True


class SafeResolver(DefaultResolver):
    """aiohttp resolver that drops every non-public DNS answer.

    aiohttp calls ``resolve()`` at connect time, so this closes the DNS-rebind
    window: even if DNS flips from public to private between our pre-check and
    connect, the connect-time validation here still rejects it.
    """

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        infos = await super().resolve(host, port, family)
        safe = []
        for entry in infos:
            try:
                ip = ipaddress.ip_address(entry["host"])
            except ValueError:
                continue
            if _is_ip_public(ip):
                safe.append(entry)
            else:
                logger.warning("SSRF blocked at resolve: %s -> %s", host, entry["host"])
        return safe


async def _resolve_public_ip(hostname: str) -> Optional[str]:
    """Pre-check: does the hostname resolve to at least one public IP?

    Used for fast-failing before we even open a connection. The real safety
    net is SafeResolver which filters at aiohttp connect time.
    """
    import asyncio

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
    except socket.gaierror:
        return None

    for _family, _, _, _, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_ip_public(ip):
            return ip_str
    logger.warning(
        "SSRF blocked: %s resolves only to non-public addresses", hostname
    )
    return None


def _validate_url(url: str) -> Optional[str]:
    """Return the normalized URL if it looks safe, None otherwise."""
    if not url or len(url) > MAX_URL_LENGTH:
        return None
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.hostname:
        return None
    return urlunparse(parsed)


async def fetch_url_safe(url: str) -> FetchResult:
    """Fetch `url` with SSRF + DNS-rebind + size guards.

    Every redirect is re-validated: we never blindly follow a `Location` that
    points at a private IP.
    """
    current = _validate_url(url)
    if current is None:
        return FetchResult(
            ok=False,
            error_code="unsafe",
            error_message="Це посилання заблоковане з міркувань безпеки.",
        )

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "uk,ru;q=0.8,en;q=0.6",
    }

    connector = aiohttp.TCPConnector(resolver=SafeResolver(), limit=20)
    async with aiohttp.ClientSession(
        timeout=REQUEST_TIMEOUT, connector=connector
    ) as session:
        for hop in range(MAX_REDIRECTS + 1):
            parsed = urlparse(current)
            ip = await _resolve_public_ip(parsed.hostname or "")
            if ip is None:
                return FetchResult(
                    ok=False,
                    error_code="unsafe",
                    error_message="Посилання веде на приватну/внутрішню мережу.",
                )

            # Connect to the resolved public IP; send original Host for TLS SNI + vhost.
            # aiohttp exposes this via `server_hostname` and the URL's host.
            try:
                async with session.get(
                    current,
                    headers=headers,
                    allow_redirects=False,
                    ssl=None,
                ) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            return FetchResult(
                                ok=False,
                                error_code="bad_redirect",
                                error_message="Сервер повернув порожній редирект.",
                                final_url=current,
                            )
                        # Resolve relative redirects against `current`
                        current = str(resp.url.join(aiohttp.yarl.URL(location)))
                        normalized = _validate_url(current)
                        if normalized is None:
                            return FetchResult(
                                ok=False,
                                error_code="unsafe",
                                error_message="Редирект на небезпечну адресу.",
                                final_url=current,
                            )
                        current = normalized
                        continue

                    if resp.status >= 400:
                        return FetchResult(
                            ok=False,
                            error_code=f"http_{resp.status}",
                            error_message=f"Сервер повернув HTTP {resp.status}.",
                            final_url=current,
                        )

                    # Early content-length reject
                    try:
                        clen = int(resp.headers.get("Content-Length", "0"))
                    except ValueError:
                        clen = 0
                    if clen and clen > MAX_BYTES:
                        return FetchResult(
                            ok=False,
                            error_code="too_large",
                            error_message="Сторінка завелика для обробки.",
                            final_url=current,
                        )

                    # Streamed read with size guard
                    buf = bytearray()
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        buf.extend(chunk)
                        if len(buf) > MAX_BYTES:
                            return FetchResult(
                                ok=False,
                                error_code="too_large",
                                error_message="Сторінка завелика для обробки.",
                                final_url=current,
                            )
                    return FetchResult(ok=True, body=bytes(buf), final_url=current)
            except aiohttp.ClientConnectorError as e:
                # SafeResolver returns an empty list for private addresses,
                # which surfaces as OSError("no matching host") — treat any
                # connector error as a likely SSRF-adjacent failure rather than
                # leaking detail to the user.
                logger.warning("fetch_url_safe connector error for %s: %s", current, e)
                return FetchResult(
                    ok=False,
                    error_code="unsafe",
                    error_message="Не вдалося підключитися до цього хоста.",
                    final_url=current,
                )
            except aiohttp.ClientError as e:
                logger.warning("fetch_url_safe client error for %s: %s", current, e)
                return FetchResult(
                    ok=False,
                    error_code="fetch_error",
                    error_message="Не вдалося завантажити сторінку.",
                    final_url=current,
                )
            except Exception as e:  # pragma: no cover - catch-all for resolver oddities
                logger.warning("fetch_url_safe unexpected error for %s: %s", current, e)
                return FetchResult(
                    ok=False,
                    error_code="fetch_error",
                    error_message="Не вдалося завантажити сторінку.",
                    final_url=current,
                )

    return FetchResult(
        ok=False,
        error_code="too_many_redirects",
        error_message="Занадто багато редиректів.",
        final_url=current,
    )
