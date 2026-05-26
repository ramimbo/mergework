from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.request_parsing import (
    optional_int,
    optional_str,
    parse_int,
    positive_bounty_id,
    positive_ledger_sequence,
    proof_hash_from_path,
    required_int,
    required_str,
)


def _detail(exc: pytest.ExceptionInfo[HTTPException]) -> str:
    return str(exc.value.detail)


def test_positive_path_values_preserve_existing_errors() -> None:
    assert positive_bounty_id(1) == 1
    assert positive_ledger_sequence(42) == 42

    with pytest.raises(HTTPException) as bounty_error:
        positive_bounty_id(0)
    assert bounty_error.value.status_code == 400
    assert _detail(bounty_error) == "bounty id must be positive"

    with pytest.raises(HTTPException) as sequence_error:
        positive_ledger_sequence(0)
    assert sequence_error.value.status_code == 400
    assert _detail(sequence_error) == "ledger sequence must be positive"


def test_proof_hash_path_parser_normalizes_hex_only() -> None:
    uppercase = "A" * 64
    assert proof_hash_from_path(uppercase) == "a" * 64

    for value in (" a" * 32, "g" * 64, "a" * 63):
        with pytest.raises(HTTPException) as exc:
            proof_hash_from_path(value)
        assert exc.value.status_code == 400
        assert _detail(exc) == "proof hash must be 64 hex characters"


def test_json_field_string_helpers_preserve_required_and_optional_behavior() -> None:
    data = {"name": "mergework", "empty": None}

    assert required_str(data, "name") == "mergework"
    assert optional_str(data, "missing", "fallback") == "fallback"
    assert optional_str(data, "empty", "fallback") == "fallback"

    with pytest.raises(HTTPException) as missing_error:
        required_str(data, "missing")
    assert _detail(missing_error) == "missing is required"

    with pytest.raises(HTTPException) as type_error:
        optional_str({"name": 123}, "name")
    assert _detail(type_error) == "name must be a string"


def test_json_field_integer_helpers_reject_bool_and_parse_strings() -> None:
    assert parse_int(" +42 ", "amount") == 42
    assert required_int({"sequence": "7"}, "sequence") == 7
    assert optional_int({}, "limit", 50) == 50

    for value in (True, "", "1.5", object()):
        with pytest.raises(HTTPException) as exc:
            parse_int(value, "amount")
        assert exc.value.status_code == 400
        assert _detail(exc) == "amount must be an integer"

    with pytest.raises(HTTPException) as missing_error:
        required_int({}, "sequence")
    assert _detail(missing_error) == "sequence must be an integer"
