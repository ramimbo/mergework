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


def test_api_examples_document_mcp_get_proof_response_shape() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert '"name":"get_proof"' in examples
    assert '"arguments":{"hash":"<proof_hash>"}' in examples
    assert '"result": {' in examples
    assert '"content": [' in examples
    assert '"type": "text"' in examples
    assert '\\"hash\\":\\"<proof_hash>\\"' in examples
    assert '\\"kind\\":\\"bounty_payment\\"' in examples
    assert "proof.issue_number" in examples


def test_api_examples_document_account_response_shape() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert "/api/v1/accounts/treasury:mrwk" in examples
    assert '"ledger_address": "github:tatelyman"' in examples
    assert '"github_login": "tatelyman"' in examples
    assert '"exists": true' in examples
    assert '"balance_mrwk": "395"' in examples
    assert "Claim GitHub balances from /me" in examples
    assert "treasury:" in examples
    assert "registered `mrwk1` addresses" in examples


def test_api_examples_document_ledger_response_shape() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert "/api/v1/ledger?limit=10" in examples
    assert "/api/v1/ledger/<sequence>" in examples
    assert '"sequence": 329' in examples
    assert '"type": "bounty_reserve"' in examples
    assert '"from": "treasury:mrwk"' in examples
    assert '"to": "reserve:bounty:36"' in examples
    assert (
        '"entry_hash": "248e1e38f90ac42897486a2b52a938ad51f31849250c4a979358e9721ec7c64e"'
        in examples
    )
    assert '"proof_hash": null' in examples
    assert "bounty-payment ledger entries" in examples


def test_api_examples_document_auth_me_response_shape() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert "/api/v1/auth/me" in examples
    assert '"authenticated": false' in examples
    assert '"github_login": null' in examples
    assert "Unauthenticated requests return" in examples


def test_api_examples_document_bounty_list_response_shape() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert "/api/v1/bounties?status=open" in examples
    assert "status` can be omitted or set to" in examples
    assert '"id": 36' in examples
    assert '"repo": "ramimbo/mergework"' in examples
    assert '"issue_number": 164' in examples
    assert '"reward_mrwk": "100"' in examples
    assert '"reserved_mrwk": "500"' in examples
    assert '"awards_paid": 4' in examples
    assert '"awards_remaining": 1' in examples
    assert "Award counters can change" in examples
    assert "Use `id` for the single-bounty API path" in examples


def test_api_examples_document_wallet_response_shape() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert "/api/v1/wallets/<wallet_address>" in examples
    assert '"address": "mrwk1fb1437aec45b46ec640f44b2e2aced55dc23556e"' in examples
    assert (
        '"public_key_hex": "d88d3edf935ba932ee2737ee5500c795f21caeb4a2fdeacb55a4ff63c52c9d51"'
        in examples
    )
    assert '"label": null' in examples
    assert '"github_login": "prettyboyvic"' in examples
    assert '"balance_mrwk": "50"' in examples
    assert '"nonce": 2' in examples
    assert '"next_nonce": 3' in examples
    assert '"created_at": "2026-05-24T17:50:56.118158"' in examples
    assert "read-only wallet lookup" in examples


def test_api_examples_document_wallet_registration_response() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert 'POST "$API_HOST/api/v1/wallets/register"' in examples
    assert "same public wallet shape" in examples
    assert '"address": "mrwk102d449a31fbb267c8f352e9968a79e3e5fc95c1b"' in examples
    assert '"label": "agent wallet"' in examples
    assert '"github_login": null' in examples
    assert '"balance_mrwk": "0"' in examples
    assert '"nonce": 0' in examples
    assert '"next_nonce": 1' in examples


def test_api_examples_document_wallet_transfer_response() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert 'POST "$API_HOST/api/v1/transfers"' in examples
    assert '"from_address":"<sender_mrwk1_address>"' in examples
    assert '"to_address":"<receiver_mrwk1_address>"' in examples
    assert '"signature_hex":"<128 lowercase hex chars>"' in examples
    assert "current `next_nonce` is `3`" in examples
    assert '"amount_microunits":1500000' in examples
    assert '"type":"mrwk_transfer_v1"' in examples
    assert "compact ASCII JSON with sorted keys" in examples
    assert '"type": "wallet_transfer"' in examples
    assert '"ledger_sequence": 42' in examples
    assert '"amount_mrwk": "1.5"' in examples
    assert '"memo": "agent payout consolidation"' in examples
    assert '"created_at": "2026-05-24T20:05:00+00:00"' in examples


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
