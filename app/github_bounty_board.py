from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError
from urllib.request import urlopen

from sqlalchemy import select

from app.db import session_scope
from app.github_issue_finalization import (
    _get_json,
    _github_issue_api_base,
    _patch_json,
)
from app.models import Bounty
from app.serializers import bounties_to_dict, public_utc_timestamp
from app.treasury import treasury_status

BOUNTY_BOARD_REPO = "ramimbo/mergework"
BOUNTY_BOARD_BLOCK_START = "<!-- mergework:bounty-board:start -->"
BOUNTY_BOARD_BLOCK_END = "<!-- mergework:bounty-board:end -->"
BOUNTY_TITLE_PREFIX_RE = re.compile(r"^MRWK bounty:\s*(?:[^-]+-\s*)?", re.IGNORECASE)
STATE_UPDATED_RE = re.compile(r"^Displayed state updated: .*$", re.MULTILINE)


def _markdown_cell(value: object) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _issue_link(issue_number: object, issue_url: object) -> str:
    return f"[#{_markdown_cell(issue_number)}]({_markdown_cell(issue_url)})"


def _work_lane(title: object) -> str:
    return _markdown_cell(BOUNTY_TITLE_PREFIX_RE.sub("", str(title or "")).strip())


def _int_value(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _claimable_bounties(open_bounties: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        bounty
        for bounty in open_bounties
        if _int_value(bounty.get("effective_awards_remaining")) > 0
    ]


def _unavailable_bounties(open_bounties: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        bounty
        for bounty in open_bounties
        if _int_value(bounty.get("effective_awards_remaining")) <= 0
    ]


def _bounty_table_row(bounty: dict[str, Any]) -> str:
    return " | ".join(
        [
            f"| {_issue_link(bounty.get('issue_number'), bounty.get('issue_url'))}",
            _work_lane(bounty.get("title")),
            f"{_markdown_cell(bounty.get('reward_mrwk'))} MRWK",
            str(_int_value(bounty.get("effective_awards_remaining"))),
            f"{_markdown_cell(bounty.get('availability_note'))} |",
        ]
    )


def _claimable_table(open_bounties: Sequence[dict[str, Any]]) -> list[str]:
    rows = [
        "| Issue | Work lane | Reward | Effective slots | Status |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for bounty in sorted(
        _claimable_bounties(open_bounties),
        key=lambda item: (
            -_int_value(item.get("effective_awards_remaining")),
            _int_value(item.get("issue_number")),
        ),
    ):
        rows.append(_bounty_table_row(bounty))
    if len(rows) == 2:
        rows.append(
            "| - | No live bounty currently has effective capacity. | - | 0 | Check opening soon. |"
        )
    return rows


def _unavailable_table(open_bounties: Sequence[dict[str, Any]]) -> list[str]:
    unavailable = _unavailable_bounties(open_bounties)
    if not unavailable:
        return []
    rows = [
        "## Open But Not Currently Claimable",
        "",
        "| Issue | Work lane | Reward | Effective slots | Status |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for bounty in sorted(unavailable, key=lambda item: _int_value(item.get("issue_number"))):
        rows.append(_bounty_table_row(bounty))
    return rows


def _pending_create_table(treasury_data: dict[str, Any]) -> list[str]:
    pending = treasury_data.get("pending_create_bounties")
    pending_rows = (
        [item for item in pending if isinstance(item, dict)] if isinstance(pending, list) else []
    )
    rows = [
        "## Opening Soon",
        "",
        (
            "These are pending `create_bounty` proposals. They are not claimable "
            "until execution creates the public bounty row and the GitHub issue "
            "gets both `mrwk:bounty` and `Reserved on MergeWork`."
        ),
        "",
        "| Issue | Work lane | Proposal | Opens after | Reserve |",
        "| --- | --- | ---: | --- | ---: |",
    ]
    for proposal in sorted(pending_rows, key=lambda item: str(item.get("executes_after") or "")):
        rows.append(
            " | ".join(
                [
                    f"| {_issue_link(proposal.get('issue_number'), proposal.get('issue_url'))}",
                    _work_lane(proposal.get("title")),
                    _markdown_cell(proposal.get("proposal_id")),
                    _markdown_cell(proposal.get("executes_after")),
                    f"{_markdown_cell(proposal.get('reserve_mrwk'))} MRWK |",
                ]
            )
        )
    if len(rows) == 6:
        rows.append("| - | No pending create-bounty proposals. | - | - | - |")
    return rows


def _treasury_capacity_lines(treasury_data: dict[str, Any]) -> list[str]:
    available = treasury_data.get("available_create_reserve_mrwk")
    next_release = treasury_data.get("next_projected_capacity_release_at")
    lines = ["## Treasury Capacity", ""]
    if available is not None:
        lines.append(f"Available create reserve: {_markdown_cell(available)} MRWK.")
    if next_release:
        lines.append(f"Next projected capacity release: {_markdown_cell(next_release)}.")
    if len(lines) == 2:
        lines.append("Treasury capacity is unavailable in the current public status payload.")
    return lines


def render_bounty_board(
    *,
    open_bounties: Sequence[dict[str, Any]],
    treasury_status: dict[str, Any],
    checked_at: str,
) -> str:
    sections = [
        BOUNTY_BOARD_BLOCK_START,
        f"Displayed state updated: {_markdown_cell(checked_at)}",
        "",
        "This board issue itself is not a bounty. Do not submit `/claim` here.",
        "",
        "## Claimable Now",
        "",
        *_claimable_table(open_bounties),
        "",
    ]
    unavailable = _unavailable_table(open_bounties)
    if unavailable:
        sections.extend([*unavailable, ""])
    sections.extend(
        [
            *_pending_create_table(treasury_status),
            "",
            *_treasury_capacity_lines(treasury_status),
            "",
            "## Not Claimable Here",
            "",
            "- This board issue itself is not a bounty.",
            (
                "- Proposed-work issues are intake only unless maintainers later make a "
                "separate live bounty."
            ),
            "- Pending `create_bounty` proposals are not live bounties.",
            (
                "- Pending `pay_bounty` proposals are accepted for proposal review, not "
                "paid, until a proof exists."
            ),
            (
                "- Closed, paid, exhausted, duplicate, stale, or superseded rounds are "
                "not claimable for new work."
            ),
            "",
            BOUNTY_BOARD_BLOCK_END,
        ]
    )
    return "\n".join(sections)


def _replace_board_block(existing_body: str, board_body: str) -> str:
    start = existing_body.find(BOUNTY_BOARD_BLOCK_START)
    end = existing_body.find(BOUNTY_BOARD_BLOCK_END)
    if start != -1 and end != -1 and start < end:
        end += len(BOUNTY_BOARD_BLOCK_END)
        return f"{existing_body[:start]}{board_body}{existing_body[end:]}"
    if not existing_body.strip():
        return board_body
    return f"{existing_body.rstrip()}\n\n{board_body}"


def _normalized_board_body(body: str) -> str:
    return STATE_UPDATED_RE.sub("Displayed state updated: <state timestamp>", body)


def update_bounty_board_issue(
    *,
    github_token: str,
    repo: str,
    issue_number: int | None,
    board_body: str,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    clean_token = github_token.strip()
    if not clean_token:
        return {"status": "skipped", "reason": "github issue token not configured"}
    if issue_number is None:
        return {"status": "skipped", "reason": "bounty board issue number not configured"}
    issue_api_base = _github_issue_api_base(repo, issue_number)
    try:
        issue = _get_json(opener=opener, url=issue_api_base, github_token=clean_token)
        current_body = issue.get("body")
        existing_body = current_body if isinstance(current_body, str) else ""
        updated_body = _replace_board_block(existing_body, board_body)
        if _normalized_board_body(updated_body) == _normalized_board_body(existing_body):
            return {"status": "already_current", "issue_number": issue_number}
        _patch_json(
            opener=opener,
            url=issue_api_base,
            github_token=clean_token,
            payload={"body": updated_body},
        )
    except HTTPError as exc:
        return {"status": "failed", "reason": f"github bounty board update failed: HTTP {exc.code}"}
    except (OSError, ValueError) as exc:
        return {
            "status": "failed",
            "reason": f"github bounty board update failed: {type(exc).__name__}",
        }
    return {"status": "updated", "issue_number": issue_number}


def refresh_bounty_board_issue(
    db_url: str,
    *,
    github_token: str,
    public_base_url: str,
    issue_number: int | None,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    if issue_number is None:
        return {"status": "skipped", "reason": "bounty board issue number not configured"}
    with session_scope(db_url) as session:
        bounties = session.scalars(
            select(Bounty)
            .where(Bounty.repo == BOUNTY_BOARD_REPO, Bounty.status == "open")
            .order_by(Bounty.created_at.desc(), Bounty.id.desc())
        ).all()
        open_bounties = bounties_to_dict(bounties, session=session)
        treasury_data = treasury_status(session)
    board_body = render_bounty_board(
        open_bounties=open_bounties,
        treasury_status=treasury_data,
        checked_at=public_utc_timestamp(datetime.now(UTC)),
    )
    return update_bounty_board_issue(
        github_token=github_token,
        repo=BOUNTY_BOARD_REPO,
        issue_number=issue_number,
        board_body=board_body,
        opener=opener,
    )
