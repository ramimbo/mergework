from __future__ import annotations

try:
    from datetime import UTC
except ImportError:
    from datetime import timedelta, timezone

    UTC = timezone(timedelta(0))

from collections.abc import Sequence  # noqa: E402

import sqlalchemy as sa  # noqa: E402
from alembic import op  # noqa: E402

revision = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("github_login", sa.String(length=80), unique=True),
        sa.Column("evm_address", sa.String(length=80), unique=True),
        sa.Column("display_name", sa.String(length=160)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "bounties",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("repo", sa.String(length=200), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("issue_url", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("reward_microunits", sa.Integer(), nullable=False),
        sa.Column("reserved_microunits", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("acceptance", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("repo", "issue_number", name="uq_bounty_issue"),
    )
    op.create_table(
        "ledger_entries",
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("entry_type", sa.String(length=40), nullable=False),
        sa.Column("from_account", sa.String(length=128)),
        sa.Column("to_account", sa.String(length=128)),
        sa.Column("amount_microunits", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(length=500), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bounty_id", sa.Integer(), sa.ForeignKey("bounties.id"), nullable=False),
        sa.Column("submitter_account", sa.String(length=128), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("verifier_result", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "proofs",
        sa.Column("hash", sa.String(length=64), primary_key=True),
        sa.Column(
            "ledger_sequence",
            sa.Integer(),
            sa.ForeignKey("ledger_entries.sequence"),
            nullable=False,
        ),
        sa.Column("bounty_id", sa.Integer(), sa.ForeignKey("bounties.id")),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("submissions.id")),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("public_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "webhook_events",
        sa.Column("delivery_id", sa.String(length=160), primary_key=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("processed_status", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("webhook_events")
    op.drop_table("proofs")
    op.drop_table("submissions")
    op.drop_table("ledger_entries")
    op.drop_table("bounties")
    op.drop_table("accounts")
