from __future__ import annotations

import argparse
import urllib.parse


def public_api_host(value: str) -> str:
    clean = value.strip()
    if not clean:
        raise argparse.ArgumentTypeError("api host must be a non-empty HTTP(S) URL")
    parsed = urllib.parse.urlparse(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise argparse.ArgumentTypeError("api host must be an absolute HTTP(S) URL")
    return clean
