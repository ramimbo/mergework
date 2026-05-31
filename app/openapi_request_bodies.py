from __future__ import annotations

from typing import Any


def json_request_body(schema: dict[str, Any], *, required: bool = True) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": required,
            "content": {"application/json": {"schema": schema}},
        }
    }
