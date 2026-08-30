"""Use explicitly based Demo equity for execute-soak loss controls.

Revision ID: 0015
Revises: 0014
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "demo_soak_sessions",
        sa.Column("equity_basis", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "demo_soak_sessions",
        sa.Column("equity_currency", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("demo_soak_sessions", "equity_currency")
    op.drop_column("demo_soak_sessions", "equity_basis")
