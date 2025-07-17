"""create new enum

Revision ID: e812e8d9b5d8
Revises: cfece312f3b6
Create Date: 2025-07-17 11:01:00.546169

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e812e8d9b5d8'
down_revision: Union[str, None] = 'cfece312f3b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
