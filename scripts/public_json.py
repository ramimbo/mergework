"""Shared public-JSON fetch helper for MergeWork maintenance scripts.

Several maintenance/report scripts previously copied their own small helper
around ``urllib.request.urlopen`` to read public MergeWork/GitHub JSON
(``check_bounty_issue_states``, ``check_live_bounty_closing_refs``,
``claim_inventory``, ``proposed_work_triage``, ``submission_quality_gate``).
Those copies disagreed on request headers, timeout/error handling, and decode
behavior, so any future fix to those mechanics had to be applied in several
places.

This module owns the request construction, timeout, and JSON decoding in one
place. Error *labeling* intentionally stays with each caller: the scripts
surface their own contextual messages (e.g. ``"... unavailable: ..."`` versus
``"public API request failed: ..."``), so callers keep their own ``try`` /
``except`` and only delegate the fetch + decode mechanics here.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

# Single source of truth for the maintenance-script HTTP timeout and the
# JSON ``Accept`` header several callers send.
DEFAULT_TIMEOUT_SECONDS = 30
JSON_ACCEPT_HEADERS: dict[str, str] = {"Accept": "application/json"}


def fetch_public_json(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    headers: Mapping[str, str] | None = None,
    opener: Callable[..., Any] | None = None,
    build_request: bool = True,
) -> Any:
    """Fetch ``url`` and return the decoded JSON payload.

    Args:
        url: Absolute URL to fetch.
        timeout: Socket timeout in seconds (passed to ``opener`` as a keyword).
        headers: Optional request headers (for example
            ``{"Accept": "application/json"}``). Ignored when ``build_request``
            is ``False``.
        opener: Callable used to open the URL. Defaults to
            ``urllib.request.urlopen``. Callers may pass their own module-level
            ``urlopen`` reference so test monkeypatches that target the caller's
            namespace continue to apply.
        build_request: When ``True`` (default) a :class:`urllib.request.Request`
            is built with ``headers`` and passed to ``opener``. When ``False``
            the raw ``url`` string is passed to ``opener`` instead (used by a
            caller whose tests assert on the raw URL argument).

    Returns:
        The decoded JSON value (``dict`` / ``list`` / scalar).

    Raises:
        urllib.error.URLError, OSError, TimeoutError: from the network layer.
        json.JSONDecodeError: when the response body is not valid JSON.

        Callers are expected to wrap these with their own contextual message.
    """
    open_url: Callable[..., Any] = opener if opener is not None else urllib.request.urlopen
    target: Any = urllib.request.Request(url, headers=dict(headers or {})) if build_request else url
    with open_url(target, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
