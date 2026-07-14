from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "app" / "templates"
PUBLIC_TEMPLATES = sorted(TEMPLATE_DIR.glob("*.html"))

# Visible Jinja placeholders that must never appear in rendered public HTML.
LEAKED_PLACEHOLDER_RES = (
    re.compile(r"\{\{\s*query\s*\}\}"),
    re.compile(r"\{\{\s*query_text\s*\}\}"),
)

# Mojibake / replacement glyphs often seen when typographic quotes break encoding.
MOJIBAKE_RES = (
    re.compile(r"\ufffd"),
    re.compile(r"(?:\?\?|\u00e2\u20ac|\u00c2\u00ab)"),
)

# Typographic quotes wrapping dynamic Jinja variables in user-facing notices.
NOTICE_LINE_RES = re.compile(
    r'class="notice"|Showing .* matching|Showing matches for',
    re.IGNORECASE,
)
TYPOGRAPHIC_QUOTE_RES = re.compile(r"[“”‘’]")
JINJA_VAR_RES = re.compile(r"\{\{\s*(?:query|query_text)\s*\}\}")

# Pages to render when --render is enabled (path, query dict).
RENDER_CASES: tuple[tuple[str, dict[str, str]], ...] = (
    ("/activity", {"q": "smoke-query"}),
    ("/activity", {"q": "#99", "account": "github:smoke"}),
    ("/wallets", {"q": "smoke-wallet"}),
    ("/bounties", {"q": "smoke-bounty"}),
)

# Intentional typographic quote usage grandfathered until pages normalize to ASCII.
TYPOGRAPHIC_NOTICE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "activity.html",
    }
)


def scan_template(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    rel = path.name

    for idx, line in enumerate(text.splitlines(), start=1):
        if MOJIBAKE_RES and any(r.search(line) for r in MOJIBAKE_RES):
            errors.append(f"{rel}:{idx}: mojibake/replacement characters in template line")

        if not NOTICE_LINE_RES.search(line):
            continue
        if rel in TYPOGRAPHIC_NOTICE_ALLOWLIST:
            continue
        if TYPOGRAPHIC_QUOTE_RES.search(line) and JINJA_VAR_RES.search(line):
            errors.append(
                f"{rel}:{idx}: typographic quotes around dynamic query notice; use ASCII quotes"
            )

    return errors


def scan_templates() -> list[str]:
    errors: list[str] = []
    for path in PUBLIC_TEMPLATES:
        errors.extend(scan_template(path))
    return errors


def _file_sqlite_url() -> str:
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    return f"sqlite:///{path}"


def scan_rendered_pages(database_url: str | None = None) -> list[str]:
    from fastapi.testclient import TestClient

    from app.db import create_schema
    from app.main import create_app

    if database_url is None:
        database_url = _file_sqlite_url()
    create_schema(database_url)
    errors: list[str] = []
    client = TestClient(create_app(database_url=database_url, webhook_secret="secret"))
    for path, params in RENDER_CASES:
        response = client.get(path, params=params)
        if response.status_code != 200:
            errors.append(f"{path} {params}: HTTP {response.status_code}")
            continue
        body = response.text
        for pattern in LEAKED_PLACEHOLDER_RES:
            if pattern.search(body):
                errors.append(f"{path} {params}: leaked raw placeholder {pattern.pattern}")
        if "\ufffd" in body:
            errors.append(f"{path} {params}: replacement character in rendered HTML")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-check public template text hazards.")
    parser.add_argument(
        "--render",
        action="store_true",
        help="Also render key public pages via TestClient (requires dev deps).",
    )
    args = parser.parse_args(argv)

    errors = scan_templates()
    if args.render:
        errors.extend(scan_rendered_pages())

    if errors:
        print("template text smoke failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    mode = "templates"
    if args.render:
        mode = "templates+render"
    print(f"template text smoke ok ({mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
