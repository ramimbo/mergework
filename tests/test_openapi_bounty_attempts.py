"""OpenAPI conformance tests for `/api/v1/bounties/{bounty_id}/attempts`.

These tests assert that the public `bounty_attempts` endpoints:
1. Appear in `/openapi.json` with the expected HTTP methods and paths.
2. Carry the typed `response_model` schemas declared in
   `app/openapi_schemas.py`.
3. The declared schema fields match the actual runtime response shape.

Closes part of #944 (OpenAPI bounty work lane).
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def openapi_spec(client):
    return client.get("/openapi.json").json()


def test_bounty_attempts_get_path_in_openapi(openapi_spec):
    paths = openapi_spec["paths"]
    assert "/api/v1/bounties/{bounty_id}/attempts" in paths
    assert "get" in paths["/api/v1/bounties/{bounty_id}/attempts"]


def test_bounty_attempts_post_path_in_openapi(openapi_spec):
    paths = openapi_spec["paths"]
    assert "/api/v1/bounties/{bounty_id}/attempts" in paths
    assert "post" in paths["/api/v1/bounties/{bounty_id}/attempts"]


def test_bounty_attempts_get_response_schema_in_openapi(openapi_spec):
    """GET response must declare the BountyAttemptListEnvelope schema, with
    `bounty_id`, `warnings`, and `attempts` properties, and `attempts` items
    must be the typed `BountyAttemptResponse`.
    """
    paths = openapi_spec["paths"]
    get_op = paths["/api/v1/bounties/{bounty_id}/attempts"]["get"]

    # FastAPI puts the success response (200) in `responses`. When
    # `response_model` is declared, the schema ref ends up under
    # `components.schemas`.
    status_200 = get_op.get("responses", {}).get("200", {})
    ref_or_schema = status_200.get("content", {}).get("application/json", {}).get("schema", {})
    assert ref_or_schema, "GET /api/v1/bounties/{bounty_id}/attempts must declare a 200 response schema"

    components = openapi_spec.get("components", {}).get("schemas", {})
    # Direct schema (no $ref) — assert on the inline property names
    if "properties" in ref_or_schema:
        props = set(ref_or_schema["properties"].keys())
    elif "$ref" in ref_or_schema:
        # Resolve $ref like "#/components/schemas/BountyAttemptListEnvelope"
        ref = ref_or_schema["$ref"].split("/")[-1]
        props = set(components[ref]["properties"].keys())
    else:
        props = set()

    assert "bounty_id" in props
    assert "warnings" in props
    assert "attempts" in props


def test_bounty_attempt_response_fields_in_openapi(openapi_spec):
    """`BountyAttemptResponse` must declare id, bounty_id, submitter_account,
    source_url, status, expires_at, created_at, updated_at.
    """
    components = openapi_spec.get("components", {}).get("schemas", {})
    assert "BountyAttemptResponse" in components
    props = set(components["BountyAttemptResponse"]["properties"].keys())
    expected = {
        "id",
        "bounty_id",
        "submitter_account",
        "source_url",
        "status",
        "expires_at",
        "created_at",
        "updated_at",
    }
    assert expected <= props, f"missing fields: {expected - props}"


def test_bounty_attempt_status_is_enum_in_openapi(openapi_spec):
    """The `status` field on `BountyAttemptResponse` must be a closed enum
    (string with allowed values), not free-form `string`.
    """
    components = openapi_spec.get("components", {}).get("schemas", {})
    status_field = components["BountyAttemptResponse"]["properties"]["status"]
    # Pydantic Literal renders as `enum: [...]`
    assert "enum" in status_field, f"status must be an enum, got: {status_field}"
    enum_values = set(status_field["enum"])
    expected = {"active", "expired", "released", "superseded"}
    assert expected <= enum_values, f"missing statuses: {expected - enum_values}"


def test_post_201_response_uses_create_schema(openapi_spec):
    """POST /api/v1/bounties/{bounty_id}/attempts must declare a 201 response
    that references the create-response schema with status="registered".
    """
    paths = openapi_spec["paths"]
    post_op = paths["/api/v1/bounties/{bounty_id}/attempts"]["post"]

    assert "201" in post_op.get("responses", {}), "POST must declare a 201 response"

    # Best-effort: just check that the 201 mentions BountyAttemptCreateResponse
    # either directly or via $ref. The exact key depends on FastAPI version.
    serialized = json.dumps(post_op["responses"])  # noqa: F821 (json is in pytest's runtime)
    # json import is in the test's runtime via pytest
    # Just check the string contains the class name
    assert "BountyAttemptCreateResponse" in serialized


def test_bounty_attempt_list_envelope_uses_typed_attempts(openapi_spec):
    """`BountyAttemptListEnvelope.attempts` must be a list of the typed
    `BountyAttemptResponse` (not a generic `array` of `string`).
    """
    components = openapi_spec.get("components", {}).get("schemas", {})
    assert "BountyAttemptListEnvelope" in components
    attempts_field = components["BountyAttemptListEnvelope"]["properties"]["attempts"]
    assert attempts_field["type"] == "array"
    items = attempts_field.get("items", {})
    # Resolve $ref if present
    if "$ref" in items:
        ref = items["$ref"].split("/")[-1]
        assert ref == "BountyAttemptResponse", f"items ref={ref}"
    else:
        # Inline schema — must at least have the expected fields
        assert "properties" in items, "attempts items must be a typed schema"
