from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class PublicApiModel(BaseModel):
    """Allow additive public API fields while documenting the stable core."""

    model_config = ConfigDict(extra="allow")


class SystemStatusResponse(PublicApiModel):
    name: str
    ticker: str
    genesis_supply_mrwk: str
    ledger_height: int
    active_bounties: int
    treasury_balance_mrwk: str
    current_transfer_paths: list[str]
    unsupported_public_paths: list[str]
    unsupported_public_paths_summary: str
    future_path: str
    future_path_boundary: str


class TreasuryProposalSummary(PublicApiModel):
    proposal_id: int
    proposed_by: str
    proposed_at: str
    executes_after: str


class PendingPayoutProposal(TreasuryProposalSummary):
    to_account: str | None = None
    submission_url: str | None = None
    accepted_by: str | None = None


class PendingCloseProposal(TreasuryProposalSummary):
    closed_by: str | None = None
    reference: str | None = None


class AcceptedAwardResponse(PublicApiModel):
    proof_hash: str
    proof_url: str
    ledger_sequence: int
    ledger_url: str
    account: str | None = None
    amount_mrwk: str | None = None
    submission_url: str | None = None
    accepted_by: str | None = None
    created_at: str


class BountyResponse(PublicApiModel):
    id: int
    repo: str
    issue_number: int
    issue_url: str
    title: str
    reward_mrwk: str
    available_mrwk: str
    reserved_mrwk: str
    max_awards: int
    awards_paid: int
    awards_remaining: int
    effective_available_mrwk: str
    effective_awards_remaining: int
    pending_payout_awards: int
    pending_payout_proposals: list[PendingPayoutProposal]
    pending_close_proposal: PendingCloseProposal | None = None
    availability_state: str
    availability_note: str
    status: str
    acceptance: str
    created_at: str
    accepted_awards: list[AcceptedAwardResponse] | None = None


class BountySummaryResponse(PublicApiModel):
    bounties_shown: int
    open_awards: int
    open_pool_mrwk: str
    effective_open_awards: int
    effective_open_pool_mrwk: str


class TreasuryChallengeResponse(PublicApiModel):
    id: int
    proposal_id: int
    challenger_account: str
    challenge_type: str
    status: str
    reason: str
    created_at: str


class TreasuryProposalResponse(PublicApiModel):
    id: int
    type: str
    action: str
    status: str
    payload_hash: str
    payload: dict[str, Any]
    proposed_by: str
    executed_by: str | None = None
    proposed_at: str
    executes_after: str
    executed_at: str | None = None
    executed_ledger_sequence: int | None = None
    result: dict[str, Any]
    challenges: list[TreasuryChallengeResponse]


class ActivityTotalsResponse(PublicApiModel):
    accepted_awards: int
    accepted_mrwk: str
    contributors: int


class ActivityContributorResponse(PublicApiModel):
    account: str
    accepted_awards: int
    accepted_mrwk: str
    latest_submission_url: str | None = None
    latest_bounty_repo: str | None = None
    latest_bounty_issue_number: int | None = None
    latest_bounty_issue_url: str | None = None
    latest_proof_hash: str | None = None
    latest_proof_url: str | None = None


class ActivityRecentRowResponse(PublicApiModel):
    ledger_sequence: int
    account: str
    amount_mrwk: str
    submission_url: str
    bounty_repo: str | None = None
    bounty_issue_number: int | None = None
    bounty_issue_url: str | None = None
    proof_hash: str
    proof_url: str
    bounty_id: int | None = None
    bounty_url: str | None = None
    created_at: str


class ActivityResponse(PublicApiModel):
    totals: ActivityTotalsResponse
    query: str
    contributors: list[ActivityContributorResponse]
    recent: list[ActivityRecentRowResponse]


class AcceptedWorkSummaryResponse(PublicApiModel):
    accepted_awards: int
    accepted_mrwk: str
    latest_ledger_sequence: int | None = None
    latest_submission_url: str | None = None
    latest_proof_hash: str | None = None
    latest_proof_url: str | None = None


class AccountResponse(PublicApiModel):
    account: str
    ledger_address: str
    github_login: str | None = None
    exists: bool
    balance_mrwk: str
    transfer_status: str
    accepted_work: AcceptedWorkSummaryResponse


class AcceptedWorkRowResponse(PublicApiModel):
    ledger_sequence: int
    ledger_url: str
    proof_hash: str
    proof_url: str
    amount_mrwk: str
    submission_url: str | None = None
    issue_url: str | None = None
    repo: str | None = None
    issue_number: int | None = None
    bounty_id: int | None = None
    bounty_url: str | None = None
    accepted_by: str | None = None
    created_at: str


class AccountAcceptedWorkResponse(PublicApiModel):
    account: str
    summary: AcceptedWorkSummaryResponse
    accepted_work: list[AcceptedWorkRowResponse]


class ProofResponse(PublicApiModel):
    kind: str | None = None
    bounty_id: int | None = None
    repo: str | None = None
    issue_number: int | None = None
    submission_url: str | None = None
    accepted_by: str | None = None
    to_account: str | None = None
    amount_mrwk: str | None = None
    ledger_sequence: int | None = None
    ledger_hash: str | None = None
    verifier_result: dict[str, Any] | None = None
