"""Add auto-memory extraction columns (S3).

Adds:
  * memories.source                    -- "user" (default) | "auto"
  * memories.source_conversation_id    -- nullable FK to conversations
  * users.auto_extract_enabled         -- bool, default False
  * users.auto_extract_model           -- string, default Haiku 4.5
  * users.auto_extract_min_messages    -- int, default 4

Revision ID: f6b2c3d4e5a7
Revises: e5a1b2c3d4f5
Create Date: 2026-04-12

"""

from alembic import op
import sqlalchemy as sa


revision = "f6b2c3d4e5a7"
down_revision = "e5a1b2c3d4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Memory provenance
    with op.batch_alter_table("memories") as batch:
        batch.add_column(
            sa.Column("source", sa.String(20), nullable=False, server_default="user"),
        )
        batch.add_column(
            sa.Column("source_conversation_id", sa.String(32), nullable=True),
        )
        batch.create_index(
            "ix_memories_source_conversation_id",
            ["source_conversation_id"],
        )

    # Per-user auto-extraction settings (default off)
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "auto_extract_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
        batch.add_column(
            sa.Column(
                "auto_extract_model",
                sa.String(100),
                nullable=False,
                server_default="claude-haiku-4-5-20251001",
            ),
        )
        batch.add_column(
            sa.Column(
                "auto_extract_min_messages",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("4"),
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("auto_extract_min_messages")
        batch.drop_column("auto_extract_model")
        batch.drop_column("auto_extract_enabled")

    with op.batch_alter_table("memories") as batch:
        batch.drop_index("ix_memories_source_conversation_id")
        batch.drop_column("source_conversation_id")
        batch.drop_column("source")
