from fastapi import HTTPException

from app.path_params import (
    SQLITE_INTEGER_MAX,
    issue_number_search_value,
    positive_bounty_id,
    positive_ledger_sequence,
    proof_hash_from_path,
)


def assert_bad_request(func, *args):
    try:
        func(*args)
    except HTTPException as exc:
        assert exc.status_code == 400
    else:  # pragma: no cover - defensive test helper
        raise AssertionError("expected HTTPException")


def test_issue_number_search_value_accepts_bounded_numeric_query():
    assert issue_number_search_value("340") == 340
    assert issue_number_search_value("#340") == 340
    assert issue_number_search_value(str(SQLITE_INTEGER_MAX)) == SQLITE_INTEGER_MAX


def test_issue_number_search_value_rejects_non_numeric_or_overflow_query():
    assert issue_number_search_value("") is None
    assert issue_number_search_value("#") is None
    assert issue_number_search_value(" 340") is None
    assert issue_number_search_value(" #340") is None
    assert issue_number_search_value("340a") is None
    assert issue_number_search_value(str(SQLITE_INTEGER_MAX + 1)) is None


def test_positive_bounty_id_and_ledger_sequence_validate_bounds():
    assert positive_bounty_id(1) == 1
    assert positive_ledger_sequence(SQLITE_INTEGER_MAX) == SQLITE_INTEGER_MAX

    assert_bad_request(positive_bounty_id, 0)
    assert_bad_request(positive_bounty_id, SQLITE_INTEGER_MAX + 1)
    assert_bad_request(positive_ledger_sequence, -1)
    assert_bad_request(positive_ledger_sequence, SQLITE_INTEGER_MAX + 1)


def test_proof_hash_from_path_normalizes_hex_hash():
    raw_hash = "A" * 64
    assert proof_hash_from_path(raw_hash) == "a" * 64


def test_proof_hash_from_path_rejects_whitespace_or_non_hex():
    assert_bad_request(proof_hash_from_path, " " + "a" * 64)
    assert_bad_request(proof_hash_from_path, "g" * 64)
    assert_bad_request(proof_hash_from_path, "a" * 63)
