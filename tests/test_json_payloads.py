from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from fastapi import HTTPException, Request

from app.json_payloads import json_object, optional_int, optional_str, required_int, required_str


class _JsonRequest:
    def __init__(self, payload: Any = None, *, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error

    async def json(self) -> Any:
        if self.error is not None:
            raise self.error
        return self.payload


def _detail(exc_info: pytest.ExceptionInfo[HTTPException]) -> str:
    return str(exc_info.value.detail)


def _status_code(exc_info: pytest.ExceptionInfo[HTTPException]) -> int:
    return int(exc_info.value.status_code)


def _request(payload: Any = None, *, error: Exception | None = None) -> Request:
    return cast(Request, _JsonRequest(payload, error=error))


def test_json_object_preserves_invalid_and_non_object_errors() -> None:
    assert asyncio.run(json_object(_request({"ok": True}))) == {"ok": True}

    with pytest.raises(HTTPException) as invalid:
        asyncio.run(json_object(_request(error=ValueError("bad json"))))
    with pytest.raises(HTTPException) as non_object:
        asyncio.run(json_object(_request(["not", "an", "object"])))

    assert _status_code(invalid) == 400
    assert _detail(invalid) == "invalid json body"
    assert _status_code(non_object) == 400
    assert _detail(non_object) == "json body must be an object"


def test_json_payload_string_helpers_preserve_required_and_type_errors() -> None:
    assert required_str({"title": "Bounty"}, "title") == "Bounty"
    assert optional_str({}, "source", "github") == "github"
    assert optional_str({"source": None}, "source", "github") == "github"

    with pytest.raises(HTTPException) as missing:
        required_str({}, "title")
    with pytest.raises(HTTPException) as typed:
        optional_str({"source": 1}, "source")

    assert _status_code(missing) == 400
    assert _detail(missing) == "title is required"
    assert _status_code(typed) == 400
    assert _detail(typed) == "source must be a string"


def test_json_payload_integer_helpers_preserve_string_and_control_char_errors() -> None:
    assert required_int({"nonce": " 42 "}, "nonce") == 42
    assert optional_int({}, "ttl_seconds", 3600) == 3600

    with pytest.raises(HTTPException) as boolean:
        required_int({"nonce": True}, "nonce")
    with pytest.raises(HTTPException) as decimal:
        required_int({"nonce": "1.5"}, "nonce")
    with pytest.raises(HTTPException) as controlled:
        optional_int({"ttl_seconds": "\x851"}, "ttl_seconds", 3600)

    assert _status_code(boolean) == 400
    assert _detail(boolean) == "nonce must be an integer"
    assert _status_code(decimal) == 400
    assert _detail(decimal) == "nonce must be an integer"
    assert _status_code(controlled) == 400
    assert _detail(controlled) == "ttl_seconds must not contain control characters"
