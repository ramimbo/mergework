from __future__ import annotations

import re

from fastapi import HTTPException, Request

from app.control_chars import contains_control_character

# Keep zero syntactically canonical so existing typed range validators own range errors,
# while rejecting aliases like +1, 1.0, and 01 before integer coercion.
CANONICAL_INTEGER_QUERY_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
CANONICAL_BOOLEAN_QUERY_VALUES = {"true", "false"}


def _query_param_values(request: Request, name: str) -> list[str]:
    return request.query_params.getlist(name)


def _reject_control_char_value(value: str, name: str) -> None:
    if contains_control_character(value):
        raise HTTPException(
            status_code=400,
            detail=f"{name} must not contain control characters",
        )


def reject_control_char_query_param(request: Request, name: str) -> None:
    """Reject raw control characters before FastAPI coerces query values."""
    for value in _query_param_values(request, name):
        _reject_control_char_value(value, name)


def reject_noncanonical_int_query_param(request: Request, name: str) -> None:
    """Reject non-canonical integer query spellings before FastAPI coerces them."""
    for value in _query_param_values(request, name):
        _reject_control_char_value(value, name)
        if not CANONICAL_INTEGER_QUERY_RE.fullmatch(value):
            raise HTTPException(
                status_code=400,
                detail=f"{name} must be a canonical positive integer",
            )


def reject_noncanonical_bool_query_param(request: Request, name: str) -> None:
    """Reject boolean query aliases before application code trusts coerced values."""
    for value in _query_param_values(request, name):
        _reject_control_char_value(value, name)
        if value not in CANONICAL_BOOLEAN_QUERY_VALUES:
            raise HTTPException(
                status_code=400,
                detail=f"{name} must be true or false",
            )


def reject_repeated_query_param(request: Request, name: str) -> None:
    """Reject ambiguous scalar query parameters before FastAPI chooses one value."""
    if len(_query_param_values(request, name)) > 1:
        raise HTTPException(
            status_code=400,
            detail=f"{name} must be provided at most once",
        )
