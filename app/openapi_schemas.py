"""Typed OpenAPI response models for bounty attempts endpoints.

This module exists to make the public `bounty_attempts` API self-documenting
for clients, SDK generators, and AI agents. The Pydantic models here mirror
the dicts produced by the existing `*_to_dict` and response builders in
`app/bounty_attempts.py` and `app/bounty_attempt_response.py` *exactly* — the
wire format is unchanged. The schemas are purely declarative so that
`/openapi.json` carries a complete contract.

This module deliberately does NOT import from `app/bounty_attempts` to
avoid a circular import: `bounty_attempts.py` imports the response models
from here, so this file must not import back.

Closes part of #944 (OpenAPI bounty work lane).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# All status values that can appear in `_attempt_effective_status` or in the
# `status` field of `BountyAttempt`. Kept exhaustive so OpenAPI consumers see
# a closed enum, not `string`.
AttemptStatus = Literal["active", "expired", "released", "superseded", "registered"]


class BountyAttemptResponse(BaseModel):
    """One bounty attempt as returned by `bounty_attempt_to_dict`.

    Mirrors `app.bounty_attempts.bounty_attempt_to_dict` exactly. Any change
    to that serializer must be reflected here.
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="Stable, monotonic attempt id")
    bounty_id: int = Field(..., description="The bounty this attempt targets")
    submitter_account: str = Field(..., description="Normalized MRWK account name of the submitter")
    source_url: str | None = Field(
        ...,
        description="Public URL of the work being claimed as fulfilling the bounty; null when no URL was provided",
    )
    status: AttemptStatus = Field(..., description="Effective attempt status at response time")
    expires_at: str = Field(..., description="ISO-8601 UTC timestamp at which the attempt expires")
    created_at: str = Field(..., description="ISO-8601 UTC timestamp of creation")
    updated_at: str = Field(..., description="ISO-8601 UTC timestamp of last status change")


class BountyAttemptListEnvelope(BaseModel):
    """Envelope returned by `GET /api/v1/bounties/{bounty_id}/attempts`.

    The GET endpoint returns this object, not a bare list.
    """

    model_config = ConfigDict(extra="forbid")

    bounty_id: int = Field(..., description="The bounty whose attempts are listed")
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings about the bounty at the time of the request",
    )
    attempts: list[BountyAttemptResponse] = Field(
        ..., description="The matching attempts, newest first"
    )


class BountyAttemptCreateResponse(BaseModel):
    """Envelope returned by `POST /api/v1/bounties/{bounty_id}/attempts` on success (201)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["registered"] = Field(..., description="Operation result marker")
    attempt: BountyAttemptResponse = Field(..., description="The newly-registered attempt")
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings about the bounty at the time of registration",
    )


class BountyAttemptNotAvailableResponse(BaseModel):
    """Envelope returned by `POST /api/v1/bounties/{bounty_id}/attempts` when the bounty is not claimable (409)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["not_available"] = Field(..., description="Operation result marker")
    bounty_id: int = Field(..., description="The bounty that was found to be unavailable")
    warnings: list[str] = Field(
        default_factory=list,
        description="Reasons the bounty was unavailable",
    )


class BountyAttemptDuplicateResponse(BaseModel):
    """Envelope returned by `POST /api/v1/bounties/{bounty_id}/attempts` when an active attempt already exists (409)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["duplicate"] = Field(..., description="Operation result marker")
    attempt: BountyAttemptResponse = Field(..., description="The pre-existing active attempt")
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings at the time of the duplicate response",
    )
