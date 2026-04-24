"""Safe, idempotent DB migrator for the release_command step.

Design constraint: alembic/env.py calls `asyncio.run()` internally, so we
can't invoke `command.stamp`/`command.upgrade` from our own running event
loop (nested asyncio.run is forbidden). Instead we:

  1. Use asyncpg directly (in its own run) to inspect DB state.
  2. Spawn alembic via `subprocess` so env.py gets a fresh event loop.

Behavior:
  * alembic_version exists                        -> alembic upgrade head
  * alembic_version missing AND users table exists -> stamp 001, then upgrade
  * fresh DB                                       -> plain upgrade head
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys

import asyncpg

from bot.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | migrate | %(message)s",
)
logger = logging.getLogger("migrate")


def _asyncpg_dsn(url: str) -> str:
    """SQLAlchemy URL -> bare postgres:// DSN that asyncpg understands."""
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)


async def _inspect_db() -> tuple[bool, bool]:
    """Return (has_alembic_version, has_users_table)."""
    dsn = _asyncpg_dsn(settings.database_url)
    conn = await asyncpg.connect(dsn)
    try:
        alembic = await conn.fetchval("SELECT to_regclass('public.alembic_version')")
        users = await conn.fetchval("SELECT to_regclass('public.users')")
        return bool(alembic), bool(users)
    finally:
        await conn.close()


def _run_alembic(*args: str) -> None:
    """Invoke alembic in a subprocess so env.py can own its event loop."""
    cmd = [sys.executable, "-m", "alembic", *args]
    logger.info("→ %s", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise SystemExit(
            f"alembic {' '.join(args)} failed with exit code {result.returncode}"
        )


def main() -> int:
    has_alembic, has_users = asyncio.run(_inspect_db())
    logger.info("DB state: alembic_version=%s users=%s", has_alembic, has_users)

    if not has_alembic and has_users:
        logger.info(
            "Legacy DB detected (tables exist without alembic_version) — stamping 001"
        )
        _run_alembic("stamp", "001")

    _run_alembic("upgrade", "head")
    logger.info("Migrations complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
