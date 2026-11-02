"""aceite dos termos

Revision ID: 3f2a9c71d4e8
Revises: 10ed1b53952b
Create Date: 2026-11-02 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f2a9c71d4e8'
down_revision: Union[str, Sequence[str], None] = '10ed1b53952b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('terms_accepted_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('terms_version', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('terms_version')
        batch_op.drop_column('terms_accepted_at')
