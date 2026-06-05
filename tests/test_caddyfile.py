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


def test_caddy_removes_public_server_headers() -> None:
    caddyfile = Path("Caddyfile").read_text(encoding="utf-8")

    assert "\n\t\t-Server\n" in caddyfile
    assert "\n\t\theader_down -Server\n" in caddyfile


def test_caddy_serves_canonical_and_legacy_mergework_hosts() -> None:
    caddyfile = Path("Caddyfile").read_text(encoding="utf-8")

    for host in (
        "mrwk.online",
        "www.mrwk.online",
        "api.mrwk.online",
        "mcp.mrwk.online",
        "mrwk.ltclab.site",
        "api.mrwk.ltclab.site",
        "mcp.mrwk.ltclab.site",
        "ltclab.site",
        "www.ltclab.site",
    ):
        assert host in caddyfile
