from __future__ import annotations

try:
    from datetime import UTC
except ImportError:
    from datetime import timezone, timedelta
    UTC = timezone(timedelta(0))

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0002_wallets"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column("address", sa.String(length=64), primary_key=True),
        sa.Column("public_key_hex", sa.String(length=64), unique=True, nullable=False),
        sa.Column("label", sa.String(length=160)),
        sa.Column("github_login", sa.String(length=80), unique=True),
        sa.Column("nonce", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_wallets_github_login", "wallets", ["github_login"])
    op.create_table(
        "wallet_transfers",
        sa.Column("hash", sa.String(length=64), primary_key=True),
        sa.Column(
            "ledger_sequence",
            sa.Integer(),
            sa.ForeignKey("ledger_entries.sequence"),
            nullable=False,
        ),
        sa.Column("from_address", sa.String(length=64), nullable=False),
        sa.Column("to_address", sa.String(length=64), nullable=False),
        sa.Column("amount_microunits", sa.Integer(), nullable=False),
        sa.Column("nonce", sa.Integer(), nullable=False),
        sa.Column("memo", sa.String(length=240), nullable=False),
        sa.Column("signature_hex", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_wallet_transfers_ledger_sequence", "wallet_transfers", ["ledger_sequence"])
    op.create_index("ix_wallet_transfers_from_address", "wallet_transfers", ["from_address"])
    op.create_index("ix_wallet_transfers_to_address", "wallet_transfers", ["to_address"])


def downgrade() -> None:
    op.drop_index("ix_wallet_transfers_to_address", table_name="wallet_transfers")
    op.drop_index("ix_wallet_transfers_from_address", table_name="wallet_transfers")
    op.drop_index("ix_wallet_transfers_ledger_sequence", table_name="wallet_transfers")
    op.drop_table("wallet_transfers")
    op.drop_index("ix_wallets_github_login", table_name="wallets")
    op.drop_table("wallets")
