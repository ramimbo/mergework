from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0005_treasury_proposals"
down_revision: str | None = "0004_bounty_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "treasury_proposals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("proposed_by", sa.String(length=128), nullable=False),
        sa.Column("executed_by", sa.String(length=128), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column(
            "executed_ledger_sequence",
            sa.Integer(),
            sa.ForeignKey("ledger_entries.sequence"),
            nullable=True,
        ),
        sa.Column("proposed_at", sa.DateTime(), nullable=False),
        sa.Column("executes_after", sa.DateTime(), nullable=False),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_treasury_proposals_action", "treasury_proposals", ["action"])
    op.create_index("ix_treasury_proposals_status", "treasury_proposals", ["status"])
    op.create_index("ix_treasury_proposals_payload_hash", "treasury_proposals", ["payload_hash"])
    op.create_index("ix_treasury_proposals_proposed_at", "treasury_proposals", ["proposed_at"])
    op.create_index(
        "ix_treasury_proposals_executes_after", "treasury_proposals", ["executes_after"]
    )
    op.create_index("ix_treasury_proposals_executed_at", "treasury_proposals", ["executed_at"])
    op.create_table(
        "treasury_challenges",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "proposal_id",
            sa.Integer(),
            sa.ForeignKey("treasury_proposals.id"),
            nullable=False,
        ),
        sa.Column("challenger_account", sa.String(length=128), nullable=False),
        sa.Column("challenge_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_treasury_challenges_proposal_id", "treasury_challenges", ["proposal_id"])
    op.create_index(
        "ix_treasury_challenges_challenger_account",
        "treasury_challenges",
        ["challenger_account"],
    )
    op.create_index(
        "ix_treasury_challenges_challenge_type", "treasury_challenges", ["challenge_type"]
    )
    op.create_index("ix_treasury_challenges_status", "treasury_challenges", ["status"])
    op.create_index("ix_treasury_challenges_created_at", "treasury_challenges", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_treasury_challenges_created_at", table_name="treasury_challenges")
    op.drop_index("ix_treasury_challenges_status", table_name="treasury_challenges")
    op.drop_index("ix_treasury_challenges_challenge_type", table_name="treasury_challenges")
    op.drop_index("ix_treasury_challenges_challenger_account", table_name="treasury_challenges")
    op.drop_index("ix_treasury_challenges_proposal_id", table_name="treasury_challenges")
    op.drop_table("treasury_challenges")
    op.drop_index("ix_treasury_proposals_executed_at", table_name="treasury_proposals")
    op.drop_index("ix_treasury_proposals_executes_after", table_name="treasury_proposals")
    op.drop_index("ix_treasury_proposals_proposed_at", table_name="treasury_proposals")
    op.drop_index("ix_treasury_proposals_payload_hash", table_name="treasury_proposals")
    op.drop_index("ix_treasury_proposals_status", table_name="treasury_proposals")
    op.drop_index("ix_treasury_proposals_action", table_name="treasury_proposals")
    op.drop_table("treasury_proposals")
