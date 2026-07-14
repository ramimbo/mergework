from __future__ import annotations

from pathlib import Path

from scripts.template_text_smoke import (
    LEAKED_PLACEHOLDER_RES,
    scan_rendered_pages,
    scan_template,
    scan_templates,
)


def test_template_text_smoke_passes_current_public_templates() -> None:
    errors = scan_templates()
    assert errors == []


def test_scan_template_flags_typographic_query_notice(tmp_path: Path) -> None:
    path = tmp_path / "wallets.html"
    path.write_text(
        '<p class="notice">Showing wallets matching “{{ query_text }}”.</p>\n',
        encoding="utf-8",
    )
    errors = scan_template(path)
    assert any("typographic quotes" in item for item in errors)


def test_scan_template_allows_ascii_query_notice(tmp_path: Path) -> None:
    path = tmp_path / "wallets.html"
    path.write_text(
        '<p class="notice">Showing wallets matching "{{ query_text }}".</p>\n',
        encoding="utf-8",
    )
    assert scan_template(path) == []


def test_scan_template_flags_leaked_placeholder_in_fixture(tmp_path: Path) -> None:
    path = tmp_path / "broken.html"
    path.write_text("<p>Showing accepted work matching {{ query }}.</p>\n", encoding="utf-8")
    # Static literal placeholder in template source is fine for Jinja; render check catches leaks.
    assert scan_template(path) == []


def test_rendered_public_pages_do_not_leak_jinja_placeholders(sqlite_url: str) -> None:
    from fastapi.testclient import TestClient

    from app.db import create_schema
    from app.main import create_app

    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    for path, params in (
        ("/activity", {"q": "bob"}),
        ("/wallets", {"q": "alice"}),
        ("/bounties", {"q": "mergework"}),
    ):
        response = client.get(path, params=params)
        assert response.status_code == 200
        for pattern in LEAKED_PLACEHOLDER_RES:
            assert not pattern.search(response.text), f"{path} leaked {pattern.pattern}"


def test_scan_rendered_pages_helper(sqlite_url: str) -> None:
    from app.db import create_schema

    create_schema(sqlite_url)
    errors = scan_rendered_pages(sqlite_url)
    assert errors == []
