from __future__ import annotations

from pathlib import Path

from scripts.docs_smoke import REQUIRED


def test_readme_lists_live_ltclab_urls() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "https://ltclab.site" in readme
    assert "https://mrwk.ltclab.site" in readme
    assert "https://api.mrwk.ltclab.site" in readme
    assert "https://mcp.mrwk.ltclab.site" in readme
    assert "https://github.com/ramimbo/mergework/discussions/16" in readme
    assert "docs/paid-bounties.md" in readme
    assert "docs/api-examples.md" in readme


def test_api_examples_document_internal_bounty_ids() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert "https://api.mrwk.ltclab.site" in examples
    assert "https://mcp.mrwk.ltclab.site" in examples
    assert "/api/v1/bounties/<bounty_id>" in examples
    assert '"name":"get_bounty"' in examples
    assert '"arguments":{"id":11}' in examples
    assert '"id":4' in examples
    assert "not the GitHub issue" in examples
    assert "accepted_awards" in examples
    assert "latest_submission_url" in examples
    assert "public_key_hex" in examples


def test_docs_smoke_covers_public_api_examples() -> None:
    assert "docs/api-examples.md" in REQUIRED


def test_contributing_names_docs_smoke_for_public_docs_changes() -> None:
    contributing = Path("CONTRIBUTING.md").read_text(encoding="utf-8")

    assert "python scripts/docs_smoke.py" in contributing
    assert "docs, templates, examples, or onboarding" in contributing
