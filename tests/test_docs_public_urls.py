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
    assert "public_key_hex" in examples


def test_api_examples_document_wallet_response_shape() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert "/api/v1/wallets/mrwk1..." in examples
    assert '"address": "mrwk1062bfd1805d9596242fba7630ab1a4393c7cd8c4"' in examples
    assert (
        '"public_key_hex": "93bfbebde4ba363d8e5bf8d4ac23221f76894583d84a6777ce1946c089a89e12"'
        in examples
    )
    assert '"label": "DYSfu side-income MRWK wallet"' in examples
    assert '"github_login": null' in examples
    assert '"balance_mrwk": "0"' in examples
    assert '"next_nonce": 1' in examples
    assert "`next_nonce` is the value clients should sign" in examples


def test_agent_guide_explains_internal_bounty_ids() -> None:
    guide = Path("docs/agent-guide.md").read_text(encoding="utf-8")

    assert "/api/v1/bounties/<bounty_id>" in guide
    assert "not the GitHub issue number" in guide


def test_docs_smoke_covers_public_api_examples() -> None:
    assert "docs/api-examples.md" in REQUIRED


def test_contributing_names_docs_smoke_for_public_docs_changes() -> None:
    contributing = Path("CONTRIBUTING.md").read_text(encoding="utf-8")

    assert "python scripts/docs_smoke.py" in contributing
    assert "docs, templates, examples, or onboarding" in contributing


def test_agent_guide_documents_activity_endpoint() -> None:
    guide = Path("docs/agent-guide.md").read_text(encoding="utf-8")

    assert "GET /api/v1/activity" in guide
    assert "accepted-work activity" in guide
