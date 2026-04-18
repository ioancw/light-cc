"""Drop trigger/cron/webhook columns from agent_definitions.

Agents are now purely callable personas; scheduling lives on Schedule rows.
Columns removed: trigger, cron_expression, cron_timezone, webhook_url, next_run_at.
(``last_run_at`` is kept for last-invocation display.)

Revision ID: a8b9c0d1e2f3
Revises: f6b2c3d4e5a7
Create Date: 2026-04-16

"""

from alembic import op
import sqlalchemy as sa


revision = "a8b9c0d1e2f3"
down_revision = "f6b2c3d4e5a7"
branch_labels = None
depends_on = None


_DROPPED_COLUMNS = [
    "trigger",
    "cron_expression",
    "cron_timezone",
    "webhook_url",
    "next_run_at",
]


def upgrade() -> None:
    # The index on next_run_at must go before the column, and it needs to be
    # dropped *outside* batch_alter_table -- batch mode recreates the table,
    # which would try to recreate this index against a non-existent column.
    with op.batch_alter_table("agent_definitions") as batch_op:
        batch_op.drop_index("ix_agent_definitions_next_run_at")
    with op.batch_alter_table("agent_definitions") as batch_op:
        for col in _DROPPED_COLUMNS:
            batch_op.drop_column(col)


def downgrade() -> None:
    with op.batch_alter_table("agent_definitions") as batch_op:
        batch_op.add_column(sa.Column("trigger", sa.String(20), server_default="manual"))
        batch_op.add_column(sa.Column("cron_expression", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("cron_timezone", sa.String(50), server_default="UTC"))
        batch_op.add_column(sa.Column("webhook_url", sa.Text, nullable=True))
        batch_op.add_column(sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True))
