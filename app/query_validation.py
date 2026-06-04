from __future__ import annotations

import re

from fastapi import HTTPException, Request

from app.control_chars import contains_control_character

# Keep zero syntactically canonical so existing typed range validators own range errors,
# while rejecting aliases like +1, 1.0, and 01 before integer coercion.
CANONICAL_INTEGER_QUERY_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
CANONICAL_BOOLEAN_QUERY_VALUES = {"true", "false"}


def reject_control_char_query_param(request: Request, name: str) -> None:
    """Reject raw control characters before FastAPI coerces query values."""
    for value in request.query_params.getlist(name):
        if contains_control_character(value):
            raise HTTPException(
                status_code=400,
                detail=f"{name} must not contain control characters",
            )


def reject_noncanonical_int_query_param(request: Request, name: str) -> None:
    """Reject non-canonical integer query spellings before FastAPI coerces them."""
    for value in request.query_params.getlist(name):
        if contains_control_character(value):
            raise HTTPException(
                status_code=400,
                detail=f"{name} must not contain control characters",
            )
        if not CANONICAL_INTEGER_QUERY_RE.fullmatch(value):
            raise HTTPException(
                status_code=400,
                detail=f"{name} must be a canonical positive integer",
            )


def reject_noncanonical_bool_query_param(request: Request, name: str) -> None:
    """Reject boolean query aliases before application code trusts coerced values."""
    for value in request.query_params.getlist(name):
        if contains_control_character(value):
            raise HTTPException(
                status_code=400,
                detail=f"{name} must not contain control characters",
            )
        if value not in CANONICAL_BOOLEAN_QUERY_VALUES:
            raise HTTPException(
                status_code=400,
                detail=f"{name} must be true or false",
            )


def reject_repeated_query_param(request: Request, name: str) -> None:
    """Reject ambiguous scalar query parameters before FastAPI chooses one value."""
    if len(request.query_params.getlist(name)) > 1:
        raise HTTPException(
            status_code=400,
            detail=f"{name} must be provided at most once",
        )


def reject_query_param_max_length(request: Request, name: str, max_length: int) -> None:
    """Reject oversized raw query parameter values before they reach DB filters."""
    for value in request.query_params.getlist(name):
        if len(value) > max_length:
            raise HTTPException(
                status_code=400,
                detail=f"{name} must be at most {max_length} characters",
            )
