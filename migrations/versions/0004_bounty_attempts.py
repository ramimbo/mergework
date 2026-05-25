from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0004_bounty_attempts"
down_revision: str | None = "0003_multi_award_bounties"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bounty_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bounty_id", sa.Integer(), sa.ForeignKey("bounties.id"), nullable=False),
        sa.Column("submitter_account", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.String(length=500)),
        sa.Column("branch_ref", sa.String(length=240)),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
        sa.Column("active_marker", sa.Integer()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bounty_attempts_bounty_id", "bounty_attempts", ["bounty_id"])
    op.create_index("ix_bounty_attempts_status", "bounty_attempts", ["status"])
    op.create_index("ix_bounty_attempts_expires_at", "bounty_attempts", ["expires_at"])
    op.create_index(
        "uq_bounty_attempt_active",
        "bounty_attempts",
        ["bounty_id", "submitter_account", "active_marker"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_bounty_attempt_active", table_name="bounty_attempts")
    op.drop_index("ix_bounty_attempts_expires_at", table_name="bounty_attempts")
    op.drop_index("ix_bounty_attempts_status", table_name="bounty_attempts")
    op.drop_index("ix_bounty_attempts_bounty_id", table_name="bounty_attempts")
    op.drop_table("bounty_attempts")
