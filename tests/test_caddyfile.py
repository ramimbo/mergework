from __future__ import annotations

from pathlib import Path

APP_MANAGED_SECURITY_HEADERS = (
    "Content-Security-Policy",
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
)


def test_caddy_security_headers_are_fallback_defaults() -> None:
    caddyfile = Path("Caddyfile").read_text(encoding="utf-8")

    for header in APP_MANAGED_SECURITY_HEADERS:
        assert f"?{header}" in caddyfile
        assert f"\n\t\t{header}" not in caddyfile
