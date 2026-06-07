from __future__ import annotations

import argparse
import urllib.parse


def public_http_url(value: str, *, label: str = "URL", forbid_credentials: bool = False) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{label} must be a non-empty HTTP(S) URL")
    parsed = urllib.parse.urlparse(clean)
    if forbid_credentials and (parsed.username or parsed.password):
        raise ValueError(f"{label} must not include username or password")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must be an absolute HTTP(S) URL")
    return clean


def public_api_host(value: str) -> str:
    try:
        return public_http_url(value, label="api host")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
