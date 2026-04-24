"""Safe, idempotent DB migrator for the release_command step.

Behavior:
  1. Connect to DATABASE_URL.
  2. If the `alembic_version` table already exists   -> run `alembic upgrade head`.
  3. Else, if legacy tables exist (e.g. `users`)     -> stamp 001 (so Alembic
     knows initial tables are there), then upgrade.
  4. Else (fresh DB)                                 -> plain upgrade head.

This keeps a historical database created before Alembic was adopted working
seamlessly, while new databases still get a clean migration history.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from alembic.config import Config

from bot.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | migrate | %(message)s",
)
logger = logging.getLogger("migrate")


async def _db_state() -> tuple[bool, bool]:
    """Return (has_alembic_version, has_legacy_users)."""
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            alembic_exists = await conn.scalar(
                text("SELECT to_regclass('public.alembic_version')")
            )
            users_exists = await conn.scalar(
                text("SELECT to_regclass('public.users')")
            )
        return bool(alembic_exists), bool(users_exists)
    finally:
        await engine.dispose()


def _alembic_cfg() -> Config:
    return Config("alembic.ini")


async def main() -> int:
    cfg = _alembic_cfg()
    has_alembic, has_users = await _db_state()

    logger.info(
        "DB state: alembic_version=%s users=%s", has_alembic, has_users
    )

    if not has_alembic and has_users:
        logger.info(
            "Legacy DB detected (tables exist without alembic_version) — stamping 001"
        )
        command.stamp(cfg, "001")

    logger.info("Running: alembic upgrade head")
    command.upgrade(cfg, "head")
    logger.info("Migrations complete.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
