"""Add agent_definitions and agent_runs tables.

Revision ID: e5a1b2c3d4f5
Revises: b7e8f9a0c1d2
Create Date: 2026-04-11

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "e5a1b2c3d4f5"
down_revision = "d4f5a6b7c8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_definitions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("system_prompt", sa.Text, nullable=False),
        sa.Column("tools", sa.Text, nullable=True),
        sa.Column("max_turns", sa.Integer, server_default=sa.text("20")),
        sa.Column("timeout_seconds", sa.Integer, server_default=sa.text("300")),
        sa.Column("memory_scope", sa.String(20), server_default="user"),
        sa.Column("permissions", sa.Text, nullable=True),
        sa.Column("trigger", sa.String(20), server_default="manual"),
        sa.Column("cron_expression", sa.String(100), nullable=True),
        sa.Column("cron_timezone", sa.String(50), server_default="UTC"),
        sa.Column("webhook_url", sa.Text, nullable=True),
        sa.Column("enabled", sa.Boolean, server_default=sa.text("true")),
        sa.Column("source", sa.String(20), server_default="user"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "name", name="uq_agent_user_name"),
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("agent_id", sa.String(32), sa.ForeignKey("agent_definitions.id"), nullable=False, index=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("trigger_type", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("result", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tokens_used", sa.Integer, server_default=sa.text("0")),
        sa.Column("conversation_id", sa.String(32), sa.ForeignKey("conversations.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("agent_runs")
    op.drop_table("agent_definitions")
