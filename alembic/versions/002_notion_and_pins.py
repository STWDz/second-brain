"""notion integrations and document pins

Revision ID: 002
Revises: 001
Create Date: 2026-04-24 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # is_pinned may have been added manually in production; guard with IF NOT EXISTS.
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT FALSE"
    )

    op.create_table(
        "notion_integrations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("database_id", sa.String(64), nullable=False),
        sa.Column("auto_sync", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_synced_document_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("notion_integrations")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS is_pinned")
