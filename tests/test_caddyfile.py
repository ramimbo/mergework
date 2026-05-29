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


def test_caddyfile_has_expected_site_block() -> None:
    caddyfile = Path("Caddyfile").read_text(encoding="utf-8")
    assert "ltclab.site, www.ltclab.site, mrwk.ltclab.site" in caddyfile
    assert "api.mrwk.ltclab.site, mcp.mrwk.ltclab.site" in caddyfile
    assert "reverse_proxy app:8000" in caddyfile
    assert "encode zstd gzip" in caddyfile


def test_caddy_all_security_headers_use_question_mark_prefix() -> None:
    caddyfile = Path("Caddyfile").read_text(encoding="utf-8")
    in_header_block = False
    for line in caddyfile.splitlines():
        if "header {" in line:
            in_header_block = True
            continue
        if in_header_block:
            if line.strip() == "}":
                break
            if line.strip().startswith("#") or not line.strip():
                continue
            assert line.strip().startswith("?"), f"Header missing ? prefix: {line.strip()}"
