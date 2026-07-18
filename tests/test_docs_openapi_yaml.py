"""Ensure docs/openapi.yaml stays aligned with the live FastAPI OpenAPI surface."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_YAML = ROOT / "docs" / "openapi.yaml"


def _paths_from_openapi_yaml(text: str) -> set[str]:
    """Extract path keys from a PyYAML-style OpenAPI dump without requiring PyYAML."""
    paths: set[str] = set()
    in_paths = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line == "paths:":
            in_paths = True
            continue
        if not in_paths:
            continue
        if line and not line.startswith(" ") and line.endswith(":"):
            # Next top-level mapping (components, etc.)
            break
        if line.startswith("  /") and line.endswith(":"):
            paths.add(line.strip()[:-1])
    return paths


def test_docs_openapi_yaml_paths_exist_in_app_openapi(sqlite_url: str) -> None:
    del sqlite_url
    assert OPENAPI_YAML.is_file(), "docs/openapi.yaml must exist"
    documented = _paths_from_openapi_yaml(OPENAPI_YAML.read_text(encoding="utf-8"))
    assert documented, "docs/openapi.yaml must document at least one path"

    client = TestClient(create_app())
    app_paths = set(client.get("/openapi.json").json().get("paths", {}))

    missing = sorted(documented - app_paths)
    assert not missing, "docs/openapi.yaml documents paths missing from app OpenAPI: " + ", ".join(
        missing
    )

    # Contract anchors that previously drifted in placeholder specs
    for required in (
        "/api/v1/wallets/register",
        "/api/v1/wallets/link-github",
        "/api/v1/github/claim",
        "/api/v1/transfers",
        "/api/v1/bounties/{bounty_id}/attempts",
        "/api/v1/treasury/proposals",
    ):
        assert required in documented
        assert required in app_paths
