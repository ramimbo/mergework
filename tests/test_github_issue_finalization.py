from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

from app.github_issue_finalization import (
    LIVE_BOUNTY_STATUS_BLOCK_END,
    LIVE_BOUNTY_STATUS_BLOCK_START,
    finalize_created_bounty_issue,
    finalize_paid_bounty_issue,
)


class _FakeResponse:
    def __init__(self, body: Any) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._body).encode()


def test_finalize_created_bounty_issue_adds_label_and_claims_open_comment() -> None:
    requests: list[Request] = []
    stale_status = (
        "Status: proposed bounty. This issue is not claimable until the treasury proposal "
        "executes, the public bounty page exists, and the `Reserved on MergeWork` "
        "claims-open comment is posted."
    )

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        requests.append(request)
        if request.get_method() == "GET":
            return _FakeResponse(
                {
                    "body": (
                        "## MRWK Bounty\n\n"
                        f"{stale_status}\n\n"
                        "Reward: `50 MRWK per accepted award`\n"
                        "Max awards: `10`\n\n"
                        "## Work Needed\n\n"
                        "Normal bounty details stay here."
                    )
                }
            )
        if request.full_url.endswith("/comments"):
            return _FakeResponse(
                {"html_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-1"}
            )
        return _FakeResponse({})

    result = finalize_created_bounty_issue(
        github_token="github-token",
        public_base_url="https://mrwk.example/",
        bounty={
            "id": 123,
            "repo": "ramimbo/mergework",
            "issue_number": 77,
        },
        opener=fake_opener,
    )

    assert result == {
        "status": "updated",
        "label": "mrwk:bounty",
        "bounty_url": "https://mrwk.example/bounties/123",
        "comment_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-1",
        "issue_body_status": "updated",
    }
    assert [request.get_method() for request in requests] == ["POST", "POST", "GET", "PATCH"]
    assert requests[0].full_url == (
        "https://api.github.com/repos/ramimbo/mergework/issues/77/labels"
    )
    assert requests[0].headers["Authorization"] == "Bearer github-token"
    assert json.loads((requests[0].data or b"").decode()) == {"labels": ["mrwk:bounty"]}
    assert requests[1].full_url == (
        "https://api.github.com/repos/ramimbo/mergework/issues/77/comments"
    )
    comment_body = json.loads((requests[1].data or b"").decode())["body"]
    assert "Reserved on MergeWork: https://mrwk.example/bounties/123" in comment_body
    assert "Claims are now open" in comment_body
    assert requests[2].full_url == "https://api.github.com/repos/ramimbo/mergework/issues/77"
    assert requests[3].full_url == "https://api.github.com/repos/ramimbo/mergework/issues/77"
    issue_body = json.loads((requests[3].data or b"").decode())["body"]
    assert issue_body.startswith(LIVE_BOUNTY_STATUS_BLOCK_START)
    assert "Reserved on MergeWork: https://mrwk.example/bounties/123" in issue_body
    assert stale_status not in issue_body
    assert "Reward: `50 MRWK per accepted award`" in issue_body
    assert "Max awards: `10`" in issue_body
    assert "Normal bounty details stay here." in issue_body


def test_finalize_created_bounty_issue_replaces_existing_live_status_block() -> None:
    requests: list[Request] = []
    stale_block = "\n".join(
        [
            LIVE_BOUNTY_STATUS_BLOCK_START,
            "old status",
            LIVE_BOUNTY_STATUS_BLOCK_END,
        ]
    )

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        requests.append(request)
        if request.get_method() == "GET":
            return _FakeResponse(
                {
                    "body": (
                        f"Intro\n\n{stale_block}\n\n"
                        "Status: proposed bounty. This issue is not claimable yet.\n\n"
                        "Original details"
                    )
                }
            )
        if request.full_url.endswith("/comments"):
            return _FakeResponse(
                {"html_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-1"}
            )
        return _FakeResponse({})

    result = finalize_created_bounty_issue(
        github_token="github-token",
        public_base_url="https://mrwk.example",
        bounty={"id": 123, "repo": "ramimbo/mergework", "issue_number": 77},
        opener=fake_opener,
    )

    assert result["issue_body_status"] == "updated"
    patch_requests = [request for request in requests if request.get_method() == "PATCH"]
    assert len(patch_requests) == 1
    issue_body = json.loads((patch_requests[0].data or b"").decode())["body"]
    assert issue_body.count(LIVE_BOUNTY_STATUS_BLOCK_START) == 1
    assert issue_body.count(LIVE_BOUNTY_STATUS_BLOCK_END) == 1
    assert "old status" not in issue_body
    assert "Status: proposed bounty. This issue is not claimable yet." not in issue_body
    assert issue_body.startswith("Intro\n\n")
    assert issue_body.endswith("\n\nOriginal details")


def test_finalize_created_bounty_issue_reports_body_refresh_failure_without_blocking() -> None:
    requests: list[Request] = []

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        requests.append(request)
        if request.get_method() == "GET":
            raise HTTPError(url=request.full_url, code=503, msg="unavailable", hdrs=None, fp=None)
        if request.full_url.endswith("/comments"):
            return _FakeResponse(
                {"html_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-1"}
            )
        return _FakeResponse({})

    result = finalize_created_bounty_issue(
        github_token="github-token",
        public_base_url="https://mrwk.example",
        bounty={"id": 123, "repo": "ramimbo/mergework", "issue_number": 77},
        opener=fake_opener,
    )

    assert result == {
        "status": "updated",
        "label": "mrwk:bounty",
        "bounty_url": "https://mrwk.example/bounties/123",
        "comment_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-1",
        "issue_body_status": "failed",
        "issue_body_reason": "github issue body update failed: HTTP 503",
    }
    assert [request.get_method() for request in requests] == ["POST", "POST", "GET"]


def test_finalize_created_bounty_issue_skips_without_token() -> None:
    calls = 0

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        nonlocal calls
        calls += 1
        return _FakeResponse({})

    result = finalize_created_bounty_issue(
        github_token="",
        public_base_url="https://mrwk.example",
        bounty={"id": 123, "repo": "ramimbo/mergework", "issue_number": 77},
        opener=fake_opener,
    )

    assert result == {"status": "skipped", "reason": "github issue token not configured"}
    assert calls == 0


def test_finalize_created_bounty_issue_fails_for_invalid_bounty_target() -> None:
    calls = 0

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        nonlocal calls
        calls += 1
        return _FakeResponse({})

    result = finalize_created_bounty_issue(
        github_token="github-token",
        public_base_url="https://mrwk.example",
        bounty={"id": "bad", "repo": "ramimbo/mergework", "issue_number": 77},
        opener=fake_opener,
    )

    assert result == {"status": "failed", "reason": "bounty issue target missing or invalid"}
    assert calls == 0


def test_finalize_created_bounty_issue_reports_http_error_code() -> None:
    def failing_opener(request: Request, timeout: float) -> _FakeResponse:
        raise HTTPError(url=request.full_url, code=403, msg="forbidden", hdrs=None, fp=None)

    result = finalize_created_bounty_issue(
        github_token="github-token",
        public_base_url="https://mrwk.example",
        bounty={"id": 123, "repo": "ramimbo/mergework", "issue_number": 77},
        opener=failing_opener,
    )

    assert result == {"status": "failed", "reason": "github issue update failed: HTTP 403"}


def test_finalize_paid_bounty_issue_adds_label_comment_and_closes_open_issue() -> None:
    requests: list[Request] = []

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        requests.append(request)
        if request.full_url.endswith("/comments?per_page=100"):
            return _FakeResponse([])
        if request.get_method() == "GET":
            return _FakeResponse({"state": "open", "labels": [{"name": "mrwk:bounty"}]})
        if request.full_url.endswith("/comments"):
            return _FakeResponse(
                {"html_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-2"}
            )
        return _FakeResponse({})

    result = finalize_paid_bounty_issue(
        github_token="github-token",
        public_base_url="https://mrwk.example/",
        bounty={
            "id": 123,
            "repo": "ramimbo/mergework",
            "issue_number": 77,
        },
        opener=fake_opener,
    )

    assert result == {
        "status": "updated",
        "label": "mrwk:paid",
        "bounty_url": "https://mrwk.example/bounties/123",
        "comment_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-2",
        "closed": True,
    }
    assert [request.get_method() for request in requests] == [
        "GET",
        "POST",
        "GET",
        "POST",
        "PATCH",
    ]
    assert requests[1].full_url == (
        "https://api.github.com/repos/ramimbo/mergework/issues/77/labels"
    )
    assert json.loads((requests[1].data or b"").decode()) == {"labels": ["mrwk:paid"]}
    comment_body = json.loads((requests[3].data or b"").decode())["body"]
    assert "Filled and paid on MergeWork" in comment_body
    assert "https://mrwk.example/bounties/123" in comment_body
    assert "mergework:mrwk:paid-bounty-finalized" in comment_body
    assert requests[4].full_url == "https://api.github.com/repos/ramimbo/mergework/issues/77"
    assert json.loads((requests[4].data or b"").decode()) == {
        "state": "closed",
        "state_reason": "completed",
    }


def test_finalize_paid_bounty_issue_noops_when_already_closed_with_paid_label() -> None:
    requests: list[Request] = []

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        requests.append(request)
        return _FakeResponse({"state": "closed", "labels": [{"name": "mrwk:paid"}]})

    result = finalize_paid_bounty_issue(
        github_token="github-token",
        public_base_url="https://mrwk.example",
        bounty={"id": 123, "repo": "ramimbo/mergework", "issue_number": 77},
        opener=fake_opener,
    )

    assert result == {
        "status": "already_finalized",
        "label": "mrwk:paid",
        "bounty_url": "https://mrwk.example/bounties/123",
        "closed": True,
    }
    assert [request.get_method() for request in requests] == ["GET"]


def test_finalize_paid_bounty_issue_comments_and_closes_when_paid_label_exists_on_open_issue() -> (
    None
):
    requests: list[Request] = []

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        requests.append(request)
        if request.full_url.endswith("/comments?per_page=100"):
            return _FakeResponse([])
        if request.full_url.endswith("/comments"):
            return _FakeResponse(
                {"html_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-2"}
            )
        return _FakeResponse({"state": "open", "labels": [{"name": "mrwk:paid"}]})

    result = finalize_paid_bounty_issue(
        github_token="github-token",
        public_base_url="https://mrwk.example",
        bounty={"id": 123, "repo": "ramimbo/mergework", "issue_number": 77},
        opener=fake_opener,
    )

    assert result == {
        "status": "updated",
        "label": "mrwk:paid",
        "bounty_url": "https://mrwk.example/bounties/123",
        "comment_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-2",
        "closed": True,
    }
    assert [request.get_method() for request in requests] == ["GET", "GET", "POST", "PATCH"]
    comment_body = json.loads((requests[2].data or b"").decode())["body"]
    assert "Filled and paid on MergeWork" in comment_body
    assert "mergework:mrwk:paid-bounty-finalized" in comment_body


def test_finalize_paid_bounty_issue_reuses_marker_comment_after_close_failure() -> None:
    requests: list[Request] = []
    issue: dict[str, Any] = {"state": "open", "labels": [{"name": "mrwk:bounty"}]}
    comments: list[dict[str, Any]] = []
    patch_attempts = 0

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        nonlocal patch_attempts
        requests.append(request)
        if request.get_method() == "GET" and request.full_url.endswith("/comments?per_page=100"):
            return _FakeResponse(comments)
        if request.get_method() == "GET":
            return _FakeResponse(issue)
        if request.full_url.endswith("/labels"):
            issue["labels"] = [{"name": "mrwk:bounty"}, {"name": "mrwk:paid"}]
            return _FakeResponse({})
        if request.full_url.endswith("/comments"):
            body = json.loads((request.data or b"").decode())["body"]
            comments.append(
                {
                    "body": body,
                    "html_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-2",
                }
            )
            return _FakeResponse(comments[-1])
        if request.get_method() == "PATCH":
            patch_attempts += 1
            if patch_attempts == 1:
                raise HTTPError(
                    url=request.full_url,
                    code=500,
                    msg="temporary github failure",
                    hdrs=None,
                    fp=None,
                )
            issue["state"] = "closed"
            return _FakeResponse({})
        raise AssertionError(f"unexpected request {request.get_method()} {request.full_url}")

    first_result = finalize_paid_bounty_issue(
        github_token="github-token",
        public_base_url="https://mrwk.example",
        bounty={"id": 123, "repo": "ramimbo/mergework", "issue_number": 77},
        opener=fake_opener,
    )
    retry_result = finalize_paid_bounty_issue(
        github_token="github-token",
        public_base_url="https://mrwk.example",
        bounty={"id": 123, "repo": "ramimbo/mergework", "issue_number": 77},
        opener=fake_opener,
    )

    assert first_result == {
        "status": "failed",
        "reason": "github issue update failed: HTTP 500",
    }
    assert retry_result == {
        "status": "updated",
        "label": "mrwk:paid",
        "bounty_url": "https://mrwk.example/bounties/123",
        "comment_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-2",
        "closed": True,
    }
    comment_posts = [
        request
        for request in requests
        if request.get_method() == "POST" and request.full_url.endswith("/comments")
    ]
    assert len(comment_posts) == 1
    assert len(comments) == 1
    assert "mergework:mrwk:paid-bounty-finalized" in comments[0]["body"]


def test_finalize_paid_bounty_issue_skips_without_token() -> None:
    calls = 0

    def fake_opener(request: Request, timeout: float) -> _FakeResponse:
        nonlocal calls
        calls += 1
        return _FakeResponse({})

    result = finalize_paid_bounty_issue(
        github_token="",
        public_base_url="https://mrwk.example",
        bounty={"id": 123, "repo": "ramimbo/mergework", "issue_number": 77},
        opener=fake_opener,
    )

    assert result == {"status": "skipped", "reason": "github issue token not configured"}
    assert calls == 0
