"""Use explicitly based Demo equity for execute-soak loss controls.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
