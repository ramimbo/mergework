from __future__ import annotations

import re
from pathlib import Path

from scripts.docs_smoke import REQUIRED, _issue_template_labels, _template_field_is_required


def test_readme_lists_live_and_legacy_urls() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "https://ltclab.site" in readme
    assert "https://mrwk.online" in readme
    assert "https://api.mrwk.online" in readme
    assert "https://mcp.mrwk.online" in readme
    assert "https://mrwk.online/activity" in readme
    assert "Legacy-compatible endpoints" in readme
    assert "https://mrwk.ltclab.site" in readme
    assert "https://api.mrwk.ltclab.site" in readme
    assert "https://mcp.mrwk.ltclab.site" in readme
    assert "https://github.com/ramimbo/mergework/discussions/16" in readme
    assert "docs/paid-bounties.md" in readme
    assert "docs/api-examples.md" in readme


def test_paid_bounties_points_to_authoritative_payment_records() -> None:
    paid = Path("docs/paid-bounties.md").read_text(encoding="utf-8")

    assert "source of truth for MRWK bounty payments" in paid
    assert "not manually updated for" in paid
    assert "every payout" in paid
    assert "https://mrwk.online/activity" in paid
    assert "https://api.mrwk.online/api/v1/activity" in paid
    assert "Legacy-compatible endpoints remain available" in paid
    assert "https://mrwk.ltclab.site/activity" in paid
    assert "https://api.mrwk.ltclab.site/api/v1/activity" in paid
    assert "GET /api/v1/bounties/{id}" in paid
    assert "GET /api/v1/proofs/{proof_hash}" in paid
    assert "https://github.com/ramimbo/mergework/discussions/16" in paid
    assert not re.search(r"(?m)^\|\s*20\d{2}-\d{2}-\d{2}\b", paid)


def test_api_examples_document_internal_bounty_ids() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert "API_HOST=https://api.mrwk.online" in examples
    assert "MCP_HOST=https://mcp.mrwk.online" in examples
    assert "https://api.mrwk.ltclab.site" in examples
    assert "https://mcp.mrwk.ltclab.site" in examples
    assert "legacy-compatible" in examples
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
    squashed = " ".join(examples.split())

    assert "/api/v1/accounts/treasury:mrwk" in examples
    assert '"ledger_address": "github:tatelyman"' in examples
    assert '"github_login": "tatelyman"' in examples
    assert '"exists": true' in examples
    assert '"balance_mrwk": "395"' in examples
    assert "Claim GitHub balances from /me" in examples
    assert "treasury:" in examples
    assert "registered `mrwk1` addresses" in examples
    assert "Internal ledger accounts use the same account response shape" in examples
    assert '"account": "treasury:mrwk"' in examples
    assert '"github_login": null' in examples
    assert (
        "Treasury and reserve balances change as bounties are reserved, paid, and released"
        in squashed
    )


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


def test_admin_runbook_documents_webhook_event_limit_cap() -> None:
    runbook = Path("docs/admin-runbook.md").read_text(encoding="utf-8")

    assert "Use `limit` to control the number of delivery rows returned (`1` to `200`" in runbook
    assert "/api/v1/admin/webhook-events?status=missing_submitter&limit=200" in runbook
    assert "/api/v1/admin/webhook-events?status=missing_submitter&limit=100" not in runbook


def test_proposed_work_template_is_not_a_live_bounty_template() -> None:
    template = Path(".github/ISSUE_TEMPLATE/proposed-work.yml").read_text(encoding="utf-8")
    lowered = template.lower()

    assert 'title: "proposed work: <short scope>"' in lowered
    assert 'labels: ["proposed-work"]' in lowered
    assert "mrwk:bounty" not in lowered
    assert "not a live mrwk bounty" in lowered
    assert "do not submit `/claim`" in lowered
    assert "reference tier" in lowered


def test_bounty_issue_template_does_not_auto_mark_issue_live() -> None:
    template = Path(".github/ISSUE_TEMPLATE/bounty.yml").read_text(encoding="utf-8")

    assert "mrwk:bounty" not in _issue_template_labels(template)
    assert "Do not add the live bounty label from this template" in template


def test_issue_template_labels_parse_inline_and_block_styles() -> None:
    assert _issue_template_labels('labels: ["proposed-work", "docs"]') == {
        "proposed-work",
        "docs",
    }
    assert _issue_template_labels("labels:\n  - proposed-work\n  - docs\nbody: []") == {
        "proposed-work",
        "docs",
    }


def test_bounty_rules_document_proposed_work_lifecycle() -> None:
    rules = Path("docs/bounty-rules.md").read_text(encoding="utf-8")
    squashed = " ".join(rules.split())

    assert "## Proposed Work Requests" in rules
    assert "proposed work request is not a live MRWK bounty" in rules
    assert (
        "proposed issue -> maintainer review -> optional create_bounty proposal -> "
        "24-hour delay -> execution -> mrwk:bounty"
    ) in squashed
    assert "Reference tiers are guidance, not entitlement" in rules


def test_bounty_lifecycle_doc_is_agent_readable() -> None:
    lifecycle = Path("docs/bounty-lifecycle.md").read_text(encoding="utf-8")
    squashed = " ".join(lifecycle.split())

    assert "A GitHub issue is claimable for MRWK only when" in lifecycle
    assert "`mrwk:bounty`" in lifecycle
    assert "Reserved on MergeWork" in lifecycle
    assert "A pending create_bounty proposal is not a live bounty." in lifecycle
    assert "A pending pay_bounty proposal is not paid work." in squashed
    assert "result.github_issue_finalization" in lifecycle


def test_agent_guide_tells_agents_not_to_claim_proposed_work() -> None:
    guide = Path("docs/agent-guide.md").read_text(encoding="utf-8")

    assert "Proposed work requests are intake issues, not live bounties" in guide
    assert "Do not submit `/claim`" in guide
    assert "wait for `mrwk:bounty`" in guide


def test_admin_runbook_warns_to_validate_production_admin_token() -> None:
    runbook = Path("docs/admin-runbook.md").read_text(encoding="utf-8")

    assert "Do not execute production treasury proposals from a local `.env`" in runbook
    assert "/api/v1/admin/webhook-events?limit=1" in runbook
    assert "A label-only partial update still needs the claims-open comment" in runbook


def test_admin_runbook_documents_production_treasury_executor() -> None:
    runbook = Path("docs/admin-runbook.md").read_text(encoding="utf-8")

    assert "MERGEWORK_TREASURY_EXECUTOR_ENABLED=1" in runbook
    assert "uses the production `.env`" in runbook
    assert "docker compose logs -f treasury-executor" in runbook
    assert "Verify `result.github_issue_finalization`" in runbook


def test_api_examples_document_bounty_list_response_shape() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert "/api/v1/bounties?status=open" in examples
    assert "/api/v1/bounties?status=open&sort=available&limit=5" in examples
    assert "/api/v1/bounties/summary?status=open&q=proof" in examples
    assert "/api/v1/bounties/summary?status=open&sort=awards&limit=5" in examples
    assert "status` can be omitted or set to" in examples
    assert "`newest` is the default" in examples
    assert "by per-award reward" in examples
    assert "`available` sorts by the remaining MRWK pool" in examples
    assert "remaining award slots" in examples
    assert "Use `limit` from `1` to `200`" in examples
    assert '"id": 36' in examples
    assert '"repo": "ramimbo/mergework"' in examples
    assert '"issue_number": 164' in examples
    assert '"reward_mrwk": "100"' in examples
    assert '"available_mrwk": "100"' in examples
    assert '"reserved_mrwk": "500"' in examples
    assert '"awards_paid": 4' in examples
    assert '"awards_remaining": 1' in examples
    assert '"bounties_shown": 1' in examples
    assert '"open_awards": 2' in examples
    assert '"open_pool_mrwk": "50"' in examples
    assert "Award counters can change" in examples
    assert "capacity totals" in examples
    assert "full bounty" in examples
    assert "same optional `status`, `q`, `sort`, and" in examples
    assert "Use `id` for the single-bounty API path" in examples


def test_api_examples_document_attempt_list_response_shape() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert "/api/v1/bounties/<bounty_id>/attempts" in examples
    assert "returns the bounty id, advisory warnings, and active attempt reservations" in examples
    assert '"bounty_id": 65' in examples
    assert '"warnings": []' in examples
    assert '"attempts": [' in examples


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


def test_api_examples_document_wallet_github_link_response() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert 'POST "$API_HOST/api/v1/wallets/link-github"' in examples
    assert "signed-in session cookie" in examples
    assert '"address":"<registered_mrwk1_address>"' in examples
    assert '"signature_hex":"<128 lowercase hex chars>"' in examples
    assert "wallet-link payload" in examples
    assert "wallet's `next_nonce`" in examples
    assert '"github_login":"<signed_in_github_login>"' in examples
    assert '"type":"mrwk_link_github_v1"' in examples
    assert "compact ASCII" in examples
    assert '"github_login": "tatelyman"' in examples
    assert '"nonce": 1' in examples
    assert '"next_nonce": 2' in examples


def test_api_examples_document_github_claim_response() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert 'POST "$API_HOST/api/v1/github/claim"' in examples
    assert '"address":"<linked_mrwk1_address>"' in examples
    assert '"signature_hex":"<128 lowercase hex chars>"' in examples
    assert "signed-in session cookie" in examples
    assert "wallet's `next_nonce` value" in examples
    assert "wallet was just linked with nonce `1`" in examples
    assert '"github_login":"<signed_in_github_login>"' in examples
    assert '"type":"mrwk_claim_github_v1"' in examples
    assert '"type": "github_claim"' in examples
    assert '"from": "github:<github_login>"' in examples
    assert '"amount_mrwk": "<claimed_amount_mrwk>"' in examples
    assert '"created_at": "2026-05-24T20:05:00+00:00"' in examples
    assert '"proof_hash": null' in examples
    assert "same immutable ledger-entry shape" in examples


def test_api_examples_document_mcp_wallet_transfer_response() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert '"name":"submit_wallet_transfer"' in examples
    assert '"from_address":"<sender_mrwk1_address>"' in examples
    assert '"to_address":"<receiver_mrwk1_address>"' in examples
    assert '"signature_hex":"<128 lowercase hex chars>"' in examples
    assert "REST transfer API" in examples
    assert "result.content[0].text" in examples
    assert '\\"type\\":\\"wallet_transfer\\"' in examples
    assert '\\"ledger_sequence\\":42' in examples
    assert '\\"amount_mrwk\\":\\"1.5\\"' in examples
    assert '\\"memo\\":\\"agent payout consolidation\\"' in examples


def test_api_examples_document_mcp_lookup_response_shapes() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert '"name":"get_ledger_entry"' in examples
    assert '"arguments":{"sequence":42}' in examples
    assert "same ledger-entry shape as" in examples
    assert '\\"sequence\\":42' in examples
    assert '\\"type\\":\\"bounty_payment\\"' in examples
    assert (
        '\\"proof_hash\\":\\"a29b9cf54f2ea4734d58e9371b20234f85936e95bd8c45687f0644ad6a9e6871\\"'
        in examples
    )
    assert '"name":"get_wallet"' in examples
    assert '"arguments":{"address":"<wallet_address>"}' in examples
    assert "same public wallet shape as" in examples
    assert "wallet not found" in examples
    assert '\\"address\\":\\"<wallet_address>\\"' in examples
    assert '\\"label\\":\\"MCP wallet\\"' in examples
    assert '\\"next_nonce\\":1' in examples


def test_api_examples_document_mcp_submit_work_proof_structured_response() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert '"name":"submit_work_proof"' in examples
    assert '"issue_number":404' in examples
    assert '"repo":"ramimbo/mergework"' in examples
    assert '"format":"json"' in examples
    assert "result.structuredContent" in examples
    assert "result.content[0].text" in examples
    assert '"structuredContent": {' in examples
    assert '"availability": "open_for_submissions"' in examples
    assert '"can_submit": true' in examples
    assert '"availability_warnings": []' in examples
    assert '"reference_formats": ["Bounty #404", "Refs #404"]' in examples
    assert '"attempt_endpoint": "/api/v1/bounties/64/attempts"' in examples
    assert '"id": "confirm_award_slot"' in examples


def test_agent_guide_explains_internal_bounty_ids() -> None:
    guide = Path("docs/agent-guide.md").read_text(encoding="utf-8")

    assert "/api/v1/bounties/<bounty_id>" in guide
    assert "not the GitHub issue number" in guide


def test_docs_smoke_covers_public_api_examples() -> None:
    assert "docs/api-examples.md" in REQUIRED


def test_docs_smoke_requires_bounty_evidence_exclusion_and_duplicate_fields() -> None:
    template = Path(".github/ISSUE_TEMPLATE/bounty.yml").read_text(encoding="utf-8").lower()

    assert _template_field_is_required(template, "evidence")
    assert _template_field_is_required(template, "out_of_scope")
    assert _template_field_is_required(template, "duplicate_stale_rules")
    assert not _template_field_is_required(
        template.replace("id: evidence", "id: optional_evidence", 1), "evidence"
    )
    assert not _template_field_is_required(
        template.replace("required: true", "required: false", 1), "work"
    )


def test_contributing_names_docs_smoke_for_public_docs_changes() -> None:
    contributing = Path("CONTRIBUTING.md").read_text(encoding="utf-8")

    assert "python scripts/docs_smoke.py" in contributing
    assert "docs, templates, examples, or onboarding" in contributing


def test_agent_guide_documents_activity_endpoint() -> None:
    guide = Path("docs/agent-guide.md").read_text(encoding="utf-8")

    assert "GET /api/v1/activity" in guide
    assert "accepted-work activity" in guide


def test_api_examples_document_activity_response_shape() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert "/api/v1/activity?q=p3xill" in examples
    assert '"totals": {' in examples
    assert '"accepted_awards": 2' in examples
    assert '"accepted_mrwk": "115"' in examples
    assert '"contributors": [' in examples
    assert '"account": "github:p3xill"' in examples
    assert (
        '"latest_submission_url": "https://github.com/ramimbo/mergework/pull/226#pullrequestreview-4354910919"'
        in examples
    )
    assert '"latest_bounty_repo": "ramimbo/mergework"' in examples
    assert '"latest_bounty_issue_number": 219' in examples
    assert (
        '"latest_bounty_issue_url": "https://github.com/ramimbo/mergework/issues/219"' in examples
    )
    assert '"recent": [' in examples
    assert '"ledger_sequence": 399' in examples
    assert '"bounty_repo": "ramimbo/mergework"' in examples
    assert '"bounty_id": 37' in examples
    assert '"bounty_issue_number": 219' in examples
    assert '"bounty_issue_url": "https://github.com/ramimbo/mergework/issues/219"' in examples
    assert '"bounty_url": "/bounties/37"' in examples
    assert "bounty repo, bounty issue URL" in examples
    assert "newest ledger sequence" in examples
    assert "/api/v1/proofs/<proof_hash>" in examples


def test_api_examples_document_accepted_work_bounty_context() -> None:
    examples = Path("docs/api-examples.md").read_text(encoding="utf-8")

    assert "/api/v1/accounts/github:carpedkm/accepted-work" in examples
    assert '"bounty_id": 67' in examples
    assert '"bounty_url": "/bounties/67"' in examples
    assert "internal bounty id and public bounty URL" in examples
