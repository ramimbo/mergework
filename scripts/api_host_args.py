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
    if parsed.username or parsed.password:
        raise argparse.ArgumentTypeError("api host must not include userinfo")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("api host must be an origin URL without path or query")
    return clean
