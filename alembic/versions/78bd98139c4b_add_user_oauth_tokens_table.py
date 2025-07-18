"""add user_oauth_tokens table

Revision ID: 78bd98139c4b
Revises: e812e8d9b5d8
Create Date: 2025-07-18 11:12:28.876373

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '78bd98139c4b'
down_revision: Union[str, None] = 'e812e8d9b5d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
