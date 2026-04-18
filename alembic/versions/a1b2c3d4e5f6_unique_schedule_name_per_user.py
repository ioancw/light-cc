"""add unique schedule name per user + schedule_runs.conversation_id

Revision ID: a1b2c3d4e5f6
Revises: c3927cfe6b26
Create Date: 2026-04-03 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'c3927cfe6b26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('schedule_runs', sa.Column('conversation_id', sa.String(32), nullable=True))

    # Deduplicate any existing rows before adding constraint:
    # keep the oldest schedule for each (user_id, name) pair.
    op.execute("""
        DELETE FROM schedules WHERE id IN (
            SELECT s.id FROM schedules s
            INNER JOIN (
                SELECT user_id, name, MIN(created_at) AS min_created
                FROM schedules
                GROUP BY user_id, name
                HAVING COUNT(*) > 1
            ) dupes ON s.user_id = dupes.user_id
                   AND s.name = dupes.name
                   AND s.created_at > dupes.min_created
        )
    """)
    # SQLite requires batch mode for adding constraints to existing tables
    with op.batch_alter_table('schedules') as batch_op:
        batch_op.create_unique_constraint('uq_schedule_user_name', ['user_id', 'name'])


def downgrade() -> None:
    with op.batch_alter_table('schedules') as batch_op:
        batch_op.drop_constraint('uq_schedule_user_name', type_='unique')
    op.drop_column('schedule_runs', 'conversation_id')
