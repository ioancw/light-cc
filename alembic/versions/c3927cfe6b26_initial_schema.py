"""initial schema

Revision ID: c3927cfe6b26
Revises:
Create Date: 2026-03-28 10:07:10.624225

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3927cfe6b26'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables."""
    op.create_table(
        'users',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('email', sa.String(255), unique=True, nullable=False, index=True),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('display_name', sa.String(100), nullable=False),
        sa.Column('is_admin', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(timezone=True)),
    )

    op.create_table(
        'conversations',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('user_id', sa.String(32), sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('title', sa.String(255), default='New conversation'),
        sa.Column('model', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True)),
        sa.Column('is_deleted', sa.Boolean(), default=False),
    )

    op.create_table(
        'messages',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('conversation_id', sa.String(32), sa.ForeignKey('conversations.id'), nullable=False, index=True),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('token_count', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True)),
    )

    op.create_table(
        'usage_events',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('user_id', sa.String(32), sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('conversation_id', sa.String(32), nullable=True, index=True),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('input_tokens', sa.Integer(), default=0),
        sa.Column('output_tokens', sa.Integer(), default=0),
        sa.Column('cost_usd', sa.Float(), default=0.0),
        sa.Column('created_at', sa.DateTime(timezone=True)),
    )

    op.create_table(
        'schedules',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('user_id', sa.String(32), sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('cron_expression', sa.String(100), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true')),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True)),
    )

    op.create_table(
        'schedule_runs',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('schedule_id', sa.String(32), sa.ForeignKey('schedules.id'), nullable=False, index=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='running'),
        sa.Column('result', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True)),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tokens_used', sa.Integer(), server_default=sa.text('0')),
    )


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table('schedule_runs')
    op.drop_table('schedules')
    op.drop_table('usage_events')
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_table('users')
