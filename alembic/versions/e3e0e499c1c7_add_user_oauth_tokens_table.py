"""add user_oauth_tokens table

Revision ID: e3e0e499c1c7
Revises: 78bd98139c4b
Create Date: 2025-07-18 11:13:38.372335

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3e0e499c1c7'
down_revision: Union[str, None] = '78bd98139c4b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
