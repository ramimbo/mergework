from __future__ import annotations

import pytest
from fastapi import HTTPException, Request

from app.query_validation import (
    reject_control_char_query_param,
    reject_noncanonical_bool_query_param,
    reject_noncanonical_int_query_param,
    reject_repeated_query_param,
)


def _request(query_string: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": query_string.encode("ascii"),
        }
    )


@pytest.mark.parametrize(
    ("guard_name", "detail"),
    [
        ("control", "q must not contain control characters"),
        ("int", "limit must not contain control characters"),
        ("bool", "include_expired must not contain control characters"),
    ],
)
def test_query_guards_share_control_character_rejection(guard_name: str, detail: str) -> None:
    request = _request("q=%C2%85term&limit=%C2%851&include_expired=%C2%85true")

    with pytest.raises(HTTPException) as exc_info:
        if guard_name == "control":
            reject_control_char_query_param(request, "q")
        elif guard_name == "int":
            reject_noncanonical_int_query_param(request, "limit")
        else:
            reject_noncanonical_bool_query_param(request, "include_expired")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == detail


def test_query_guards_keep_canonical_and_repeated_boundaries() -> None:
    request = _request("limit=01&include_expired=yes&status=open&status=paid")

    with pytest.raises(HTTPException) as int_error:
        reject_noncanonical_int_query_param(request, "limit")
    assert int_error.value.status_code == 400
    assert int_error.value.detail == "limit must be a canonical positive integer"

    with pytest.raises(HTTPException) as bool_error:
        reject_noncanonical_bool_query_param(request, "include_expired")
    assert bool_error.value.status_code == 400
    assert bool_error.value.detail == "include_expired must be true or false"

    with pytest.raises(HTTPException) as repeated_error:
        reject_repeated_query_param(request, "status")
    assert repeated_error.value.status_code == 400
    assert repeated_error.value.detail == "status must be provided at most once"
