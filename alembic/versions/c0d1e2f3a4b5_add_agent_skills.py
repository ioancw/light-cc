"""Add skills column to agent_definitions for skill-first composition.

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-04-17

"""

from alembic import op
import sqlalchemy as sa


revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_definitions",
        sa.Column("skills", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_definitions", "skills")
