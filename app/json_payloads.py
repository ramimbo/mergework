from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from app.control_chars import contains_control_character


async def json_object(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid json body") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="json body must be an object")
    return data


def required_str(data: dict[str, Any], field: str) -> str:
    if field not in data or data[field] is None:
        raise HTTPException(status_code=400, detail=f"{field} is required")
    value = data[field]
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    return value


def optional_str(data: dict[str, Any], field: str, default: str = "") -> str:
    value = data.get(field, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    return value


def _parse_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if contains_control_character(value):
            raise HTTPException(
                status_code=400, detail=f"{field} must not contain control characters"
            )
        clean = value.strip()
        if clean and clean.lstrip("+-").isdigit():
            try:
                return int(clean)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"{field} must be an integer") from exc
    raise HTTPException(status_code=400, detail=f"{field} must be an integer")


def required_int(data: dict[str, Any], field: str) -> int:
    value = data.get(field)
    if value is None:
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    return _parse_int(value, field)


def optional_int(data: dict[str, Any], field: str, default: int) -> int:
    value = data.get(field, default)
    if value is None:
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    return _parse_int(value, field)
