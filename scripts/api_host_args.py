from __future__ import annotations

import argparse
import json
import subprocess
import urllib.parse
from typing import Any


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


def run_readonly_gh_json(
    args: list[str],
    *,
    timeout_seconds: int | float,
) -> Any:
    command = " ".join(args)
    try:
        completed = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"gh command timed out after {timeout_seconds}s: {command}") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            "GitHub CLI executable 'gh' was not found; install gh and ensure it is on PATH "
            "before using live --repo mode"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "gh command failed "
            f"(exit {exc.returncode}): {command}\n"
            f"stdout:\n{exc.stdout or exc.output or ''}\n"
            f"stderr:\n{exc.stderr or ''}"
        ) from exc
    return json.loads(completed.stdout)
