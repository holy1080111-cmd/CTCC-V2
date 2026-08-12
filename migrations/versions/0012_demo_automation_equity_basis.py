"""Track the capital basis used by Demo automation risk controls.

Revision ID: 0012
Revises: 0011
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "demo_automation_state",
        sa.Column("equity_basis", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("demo_automation_state", "equity_basis")
