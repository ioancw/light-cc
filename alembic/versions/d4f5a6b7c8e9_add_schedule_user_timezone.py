"""add user_timezone column to schedules

Revision ID: d4f5a6b7c8e9
Revises: b7e8f9a0c1d2
Create Date: 2026-04-09 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f5a6b7c8e9'
down_revision: Union[str, Sequence[str], None] = 'b7e8f9a0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('schedules', sa.Column('user_timezone', sa.String(50), server_default='UTC', nullable=False))


def downgrade() -> None:
    op.drop_column('schedules', 'user_timezone')
