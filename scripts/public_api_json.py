"""Shared JSON fetch + shape validation for MergeWork public API reads."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 30


def fetch_public_json(url: str, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except (TimeoutError, urllib.error.URLError) as exc:
        raise RuntimeError(f"public API request failed: {url}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"public API returned invalid JSON from {url}") from exc


def ensure_json_list(data: Any, *, url: str, label: str = "response") -> list[Any]:
    if not isinstance(data, list):
        raise RuntimeError(
            f"expected a JSON list for {label} from {url}, got {type(data).__name__}"
        )
    return data


def ensure_json_object(data: Any, *, url: str, label: str = "response") -> dict[str, Any]:
    if not isinstance(data, dict):
        raise RuntimeError(
            f"expected a JSON object for {label} from {url}, got {type(data).__name__}"
        )
    return data


def dict_rows(data: list[Any], *, url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in ensure_json_list(data, url=url):
        if isinstance(item, dict):
            rows.append(item)
    return rows


def load_public_bounty_list(
    api_host: str, *, query: str = "status=open&limit=200"
) -> list[dict[str, Any]]:
    url = f"{api_host.rstrip('/')}/api/v1/bounties?{query}"
    return dict_rows(fetch_public_json(url), url=url)


def validate_public_activity(activity: dict[str, Any], *, url: str) -> dict[str, Any]:
    contributors = activity.get("contributors")
    recent = activity.get("recent")
    if contributors is not None and not isinstance(contributors, list):
        raise RuntimeError(
            f"expected contributors list from {url}, got {type(contributors).__name__}"
        )
    if recent is not None and not isinstance(recent, list):
        raise RuntimeError(f"expected recent list from {url}, got {type(recent).__name__}")
    return activity


def load_public_activity(api_host: str, *, limit: int = 200) -> dict[str, Any]:
    url = f"{api_host.rstrip('/')}/api/v1/activity?limit={limit}"
    activity = ensure_json_object(fetch_public_json(url), url=url, label="activity")
    return validate_public_activity(activity, url=url)


def extract_public_api_state(bounties: Any, activity: Any) -> dict[str, Any]:
    """Best-effort shape extraction used by claim inventory live loads."""
    data: dict[str, Any] = {}
    if isinstance(bounties, list):
        data["bounties"] = bounties
    if isinstance(activity, dict):
        contributors = activity.get("contributors")
        if isinstance(contributors, list):
            data["contributors"] = contributors
        recent = activity.get("recent")
        if isinstance(recent, list):
            data["recent"] = recent
    return data


def load_public_api_state(api_host: str, *, limit: int = 200) -> dict[str, Any]:
    host = api_host.rstrip("/")
    bounties_url = f"{host}/api/v1/bounties?limit={limit}"
    activity_url = f"{host}/api/v1/activity?limit={limit}"
    bounties = fetch_public_json(bounties_url)
    activity = fetch_public_json(activity_url)
    if isinstance(activity, dict):
        validate_public_activity(activity, url=activity_url)
    return extract_public_api_state(bounties, activity)
