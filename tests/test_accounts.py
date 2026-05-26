from __future__ import annotations

import pytest

from app.accounts import (
    AccountError,
    account_summary,
    github_login_from_account,
    normalize_account,
    transfer_status_for_account,
)


@pytest.mark.parametrize(
    ("raw_account", "normalized"),
    [
        (" Treasury:MRWK ", "treasury:mrwk"),
        ("Reserve:Bounty:001", "reserve:bounty:1"),
        ("GitHub:Alice", "github:alice"),
        ("MRWK1" + ("A" * 40), "mrwk1" + ("a" * 40)),
    ],
)
def test_normalize_account_canonicalizes_known_account_types(
    raw_account: str, normalized: str
) -> None:
    assert normalize_account(raw_account) == normalized


@pytest.mark.parametrize(
    "raw_account",
    [
        "",
        "github: ",
        "treasury:ops",
        "reserve:wallet:1",
        "reserve:bounty:0",
        "mrwk1bad",
        "test\x00account",
    ],
)
def test_normalize_account_rejects_invalid_account_values(raw_account: str) -> None:
    with pytest.raises(AccountError):
        normalize_account(raw_account)


def test_account_helpers_shape_api_summary() -> None:
    accepted_work = {
        "accepted_awards": 1,
        "accepted_mrwk": "25",
        "latest_ledger_sequence": 3,
    }

    summary = account_summary(
        "github:alice",
        exists=True,
        balance_mrwk="25",
        accepted_work=accepted_work,
    )

    assert github_login_from_account("github:alice") == "alice"
    assert github_login_from_account("treasury:mrwk") is None
    assert transfer_status_for_account("github:alice").startswith("Claim GitHub balances")
    assert transfer_status_for_account("treasury:mrwk").startswith("Internal ledger account")
    assert summary == {
        "account": "github:alice",
        "ledger_address": "github:alice",
        "github_login": "alice",
        "exists": True,
        "balance_mrwk": "25",
        "transfer_status": transfer_status_for_account("github:alice"),
        "accepted_work": accepted_work,
    }
