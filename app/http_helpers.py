from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit
from typing import Any

from fastapi import HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Proof, Submission


def _request_was_forwarded_https(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        return forwarded_proto.split(",", 1)[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _preserve_forwarded_https_redirect(request: Request, response: Response) -> None:
    if response.status_code not in {307, 308} or not _request_was_forwarded_https(request):
        return
    location = response.headers.get("location")
    if not location:
        return
    parsed = urlsplit(location)
    if parsed.scheme != "http" or parsed.netloc != request.url.netloc:
        return
    response.headers["location"] = urlunsplit(
        ("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment)
    )


def _host_without_port(request: Request) -> str:
    return request.headers.get("host", "").split(":", 1)[0].lower()


def _is_ltc_lab_host(request: Request) -> bool:
    return _host_without_port(request) in {"ltclab.site", "www.ltclab.site"}


def _existing_payout_proof_for_submission(
    session: Session, bounty_id: int, submission_url: str
) -> Proof | None:
    submission = session.scalar(
        select(Submission)
        .where(Submission.bounty_id == bounty_id, Submission.url == submission_url)
        .limit(1)
    )
    if submission is None:
        return None
    return session.scalar(
        select(Proof)
        .where(Proof.submission_id == submission.id, Proof.kind == "bounty_payment")
        .limit(1)
    )


def _payout_response_from_proof(proof: Proof, *, status: str) -> dict[str, Any]:
    data = json.loads(proof.public_json)
    if not isinstance(data, dict) or data.get("kind") != "bounty_payment":
        raise HTTPException(status_code=500, detail="invalid proof payload")
    return {
        "status": status,
        "bounty_id": proof.bounty_id,
        "to_account": data.get("to_account"),
        "submission_id": proof.submission_id,
        "submission_url": data.get("submission_url"),
        "ledger_sequence": proof.ledger_sequence,
        "ledger_url": f"/ledger/{proof.ledger_sequence}",
        "proof_hash": proof.hash,
        "proof_url": f"/proofs/{proof.hash}",
    }
