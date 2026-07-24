from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class PublicJsonError(RuntimeError):
    """Raised when a public JSON endpoint cannot be fetched or decoded."""


def load_public_json(
    url: str,
    *,
    description: str | None = None,
    timeout: float = 30,
    user_agent: str = "mergework-maintenance-script",
    accept: str = "application/json",
) -> Any:
    label = description or "public JSON"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": user_agent,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise PublicJsonError(f"{label} unavailable: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PublicJsonError(f"{label} unavailable: {exc}") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PublicJsonError(f"{label} unavailable: invalid JSON") from exc
