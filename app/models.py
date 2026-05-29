from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    github_login: Mapped[str | None] = mapped_column(String(80), unique=True)
    evm_address: Mapped[str | None] = mapped_column(String(80), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class Wallet(Base):
    __tablename__ = "wallets"

    address: Mapped[str] = mapped_column(String(64), primary_key=True)
    public_key_hex: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str | None] = mapped_column(String(160))
    github_login: Mapped[str | None] = mapped_column(String(80), unique=True, index=True)
    nonce: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class Bounty(Base):
    __tablename__ = "bounties"
    __table_args__ = (UniqueConstraint("repo", "issue_number", name="uq_bounty_issue"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo: Mapped[str] = mapped_column(String(200), index=True)
    issue_number: Mapped[int] = mapped_column(Integer, index=True)
    issue_url: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(String(300))
    reward_microunits: Mapped[int] = mapped_column(Integer)
    reserved_microunits: Mapped[int] = mapped_column(Integer, default=0)
    max_awards: Mapped[int] = mapped_column(Integer, default=1)
    awards_paid: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40), default="open", index=True)
    acceptance: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    submissions: Mapped[list[Submission]] = relationship(back_populates="bounty")
    attempts: Mapped[list[BountyAttempt]] = relationship(back_populates="bounty")


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("bounty_id", "url", name="uq_submission_bounty_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bounty_id: Mapped[int] = mapped_column(ForeignKey("bounties.id"), index=True)
    submitter_account: Mapped[str] = mapped_column(String(128), index=True)
    url: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(40), default="submitted", index=True)
    verifier_result: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    bounty: Mapped[Bounty] = relationship(back_populates="submissions")


class BountyAttempt(Base):
    __tablename__ = "bounty_attempts"
    __table_args__ = (
        Index(
            "uq_active_bounty_attempt_submitter",
            "bounty_id",
            "submitter_account",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bounty_id: Mapped[int] = mapped_column(ForeignKey("bounties.id"), index=True)
    submitter_account: Mapped[str] = mapped_column(String(128), index=True)
    source_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)
    bounty: Mapped[Bounty] = relationship(back_populates="attempts")


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_type: Mapped[str] = mapped_column(String(40), index=True)
    from_account: Mapped[str | None] = mapped_column(String(128), index=True)
    to_account: Mapped[str | None] = mapped_column(String(128), index=True)
    amount_microunits: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(500))
    previous_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class WalletTransfer(Base):
    __tablename__ = "wallet_transfers"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    ledger_sequence: Mapped[int] = mapped_column(ForeignKey("ledger_entries.sequence"), index=True)
    from_address: Mapped[str] = mapped_column(String(64), index=True)
    to_address: Mapped[str] = mapped_column(String(64), index=True)
    amount_microunits: Mapped[int] = mapped_column(Integer)
    nonce: Mapped[int] = mapped_column(Integer)
    memo: Mapped[str] = mapped_column(String(240), default="")
    signature_hex: Mapped[str] = mapped_column(String(128))
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class Proof(Base):
    __tablename__ = "proofs"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    ledger_sequence: Mapped[int] = mapped_column(ForeignKey("ledger_entries.sequence"), index=True)
    bounty_id: Mapped[int | None] = mapped_column(ForeignKey("bounties.id"), index=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("submissions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    public_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    delivery_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    processed_status: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class TreasuryProposal(Base):
    __tablename__ = "treasury_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    proposed_by: Mapped[str] = mapped_column(String(128))
    executed_by: Mapped[str | None] = mapped_column(String(128))
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    executed_ledger_sequence: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_entries.sequence")
    )
    proposed_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    executes_after: Mapped[datetime] = mapped_column(index=True)
    executed_at: Mapped[datetime | None] = mapped_column(index=True)
    challenges: Mapped[list[TreasuryChallenge]] = relationship(back_populates="proposal")


class TreasuryChallenge(Base):
    __tablename__ = "treasury_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("treasury_proposals.id"), index=True)
    challenger_account: Mapped[str] = mapped_column(String(128), index=True)
    challenge_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    proposal: Mapped[TreasuryProposal] = relationship(back_populates="challenges")
