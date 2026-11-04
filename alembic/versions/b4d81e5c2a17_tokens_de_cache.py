"""tokens de cache

Revision ID: b4d81e5c2a17
Revises: 7c1e4b2a9f30
Create Date: 2026-11-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4d81e5c2a17'
down_revision: Union[str, Sequence[str], None] = '7c1e4b2a9f30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('llm_calls', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cached_tokens', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('llm_calls', schema=None) as batch_op:
        batch_op.drop_column('cached_tokens')
