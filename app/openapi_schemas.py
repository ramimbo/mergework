"""Typed OpenAPI response models for bounty attempts endpoints.

Closes part of #944 (OpenAPI bounty work lane) — gives `bounty_attempts`
endpoints explicit `response_model` declarations so they appear in
`/openapi.json` with full schema, making the API self-documenting for
SDK generators and agent clients.

The shapes here match what `bounty_attempt_to_dict` already returns
runtime, so the wire format is unchanged. The schemas are purely
declarative — they exist so OpenAPI consumers (Claude agents, code
generators, OpenAPI client SDKs) can understand the contract without
reading Python source.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# Status values that may appear in `_attempt_effective_status`. The model
# has to cover all of them so the OpenAPI enum is exhaustive.
AttemptStatus = Literal["active", "expired", "released", "superseded"]


class BountyAttemptResponse(BaseModel):
    """One bounty attempt as returned by `/api/v1/bounties/{bounty_id}/attempts`.

    Mirrors the dict produced by `app.bounty_attempts.bounty_attempt_to_dict`
    exactly. Any change to that serializer must be reflected here.
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="Stable, monotonic attempt id")
    bounty_id: int = Field(..., description="The bounty this attempt targets")
    submitter_account: str = Field(..., description="Normalized MRWK account name of the submitter")
    source_url: str = Field(..., description="Public URL of the work being claimed as fulfilling the bounty")
    status: AttemptStatus = Field(..., description="Effective attempt status at response time")
    expires_at: str = Field(..., description="ISO-8601 UTC timestamp at which the attempt expires")
    created_at: str = Field(..., description="ISO-8601 UTC timestamp of creation")
    updated_at: str = Field(..., description="ISO-8601 UTC timestamp of last status change")


class BountyAttemptListResponse(BaseModel):
    """Top-level shape returned by `GET /api/v1/bounties/{bounty_id}/attempts`.

    The list endpoint currently returns the bare list, not an envelope.
    We document it as an array-of-`BountyAttemptResponse` so OpenAPI
    consumers can iterate without inspecting the response.
    """

    model_config = ConfigDict(extra="forbid")


# FastAPI can take a `list[BountyAttemptResponse]` directly in
# `response_model=`, but we also export the alias for clarity in callers.
BountyAttemptList = list[BountyAttemptResponse]
