"""create new enum

Revision ID: cfece312f3b6
Revises: d2967f715d54
Create Date: 2025-07-17 10:58:06.167631

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cfece312f3b6'
down_revision: Union[str, None] = 'd2967f715d54'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
