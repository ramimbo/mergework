from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0003_multi_award_bounties"
down_revision: str | None = "0002_wallets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bounties",
        sa.Column("max_awards", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "bounties",
        sa.Column("awards_paid", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE bounties SET awards_paid = 1 WHERE status = 'paid'")
    op.create_index(
        "uq_submission_bounty_url",
        "submissions",
        ["bounty_id", "url"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_submission_bounty_url", table_name="submissions")
    op.drop_column("bounties", "awards_paid")
    op.drop_column("bounties", "max_awards")
