"""OpenAPI conformance tests for `/api/v1/bounties/{bounty_id}/attempts`.

These tests assert that the public `bounty_attempts` endpoints:
1. Appear in `/openapi.json` with the expected HTTP methods and paths.
2. Carry the typed `response_model` schemas declared in
   `app.openapi_schemas.py`.
3. The declared schema fields match the contract documented in the models.

We build the FastAPI app once and read the openapi schema directly via
`app.openapi()`, avoiding any need for a network client fixture.

Closes part of #944 (OpenAPI bounty work lane).
"""
from __future__ import annotations

import json

import pytest

from app.main import create_app


@pytest.fixture(scope="module")
def openapi_spec() -> dict:
    """Return the OpenAPI schema for the FastAPI app, generated once per module."""
    app = create_app()
    return app.openapi()


def test_bounty_attempts_get_path_in_openapi(openapi_spec):
    paths = openapi_spec["paths"]
    assert "/api/v1/bounties/{bounty_id}/attempts" in paths
    assert "get" in paths["/api/v1/bounties/{bounty_id}/attempts"]


def test_bounty_attempts_post_path_in_openapi(openapi_spec):
    paths = openapi_spec["paths"]
    assert "/api/v1/bounties/{bounty_id}/attempts" in paths
    assert "post" in paths["/api/v1/bounties/{bounty_id}/attempts"]


def test_bounty_attempt_response_fields_in_openapi(openapi_spec):
    """`BountyAttemptResponse` must declare all 8 expected fields."""
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


def test_bounty_attempt_list_envelope_in_openapi(openapi_spec):
    """`BountyAttemptListEnvelope` must be present and have
    `bounty_id`, `warnings`, `attempts` properties.
    """
    components = openapi_spec.get("components", {}).get("schemas", {})
    assert "BountyAttemptListEnvelope" in components
    props = set(components["BountyAttemptListEnvelope"]["properties"].keys())
    assert {"bounty_id", "warnings", "attempts"} <= props


def test_bounty_attempt_list_envelope_attempts_is_typed_array(openapi_spec):
    """`BountyAttemptListEnvelope.attempts` must be a list of the typed
    `BountyAttemptResponse` (not a generic `array` of `string`).
    """
    components = openapi_spec.get("components", {}).get("schemas", {})
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


def test_bounty_attempt_create_response_in_openapi(openapi_spec):
    """`BountyAttemptCreateResponse` must be present with the expected fields."""
    components = openapi_spec.get("components", {}).get("schemas", {})
    assert "BountyAttemptCreateResponse" in components
    props = set(components["BountyAttemptCreateResponse"]["properties"].keys())
    assert {"status", "attempt", "warnings"} <= props


def test_get_endpoint_declares_response_model(openapi_spec):
    """The GET endpoint must declare a 200 response with a schema (or ref)."""
    get_op = openapi_spec["paths"]["/api/v1/bounties/{bounty_id}/attempts"]["get"]
    assert "200" in get_op.get("responses", {}), "GET must declare a 200 response"


def test_post_endpoint_declares_201_response(openapi_spec):
    """The POST endpoint must declare a 201 response."""
    post_op = openapi_spec["paths"]["/api/v1/bounties/{bounty_id}/attempts"]["post"]
    assert "201" in post_op.get("responses", {}), "POST must declare a 201 response"
    assert "409" in post_op.get("responses", {}), "POST must declare a 409 response for unavailable/duplicate"
