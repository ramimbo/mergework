from __future__ import annotations

from pathlib import Path

WORKFLOW_PATH = Path(".github/workflows/ci.yml")


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _position(workflow: str, snippet: str) -> int:
    position = workflow.find(snippet)
    assert position != -1, f"missing CI workflow snippet: {snippet}"
    return position


def _env_value(workflow: str, name: str) -> str:
    line_start = _position(workflow, f"{name}:")
    line_end = workflow.find("\n", line_start)
    line = workflow[line_start:] if line_end == -1 else workflow[line_start:line_end]
    _, value = line.split(":", 1)
    value = value.strip()
    assert value, f"{name} must have a value in the CI deploy-readiness env"
    return value


def test_ci_workflow_preserves_quality_gate_steps() -> None:
    workflow = _workflow_text()

    assert "pull_request:" in workflow
    assert "branches: [main]" in workflow
    assert 'python-version: "3.12"' in workflow

    expected_steps = [
        "python -m pip install -e '.[dev]'",
        "python scripts/check_agents.py",
        "pytest",
        "ruff format --check .",
        "ruff check .",
        "mypy app",
        "python scripts/check_deploy_ready.py",
        "python scripts/docs_smoke.py",
        "docker build -t mergework:ci .",
    ]

    positions = [_position(workflow, step) for step in expected_steps]

    assert positions == sorted(positions)


def test_ci_deploy_readiness_step_uses_strong_dummy_environment() -> None:
    workflow = _workflow_text()

    required_env = {
        "MERGEWORK_DATABASE_URL",
        "MERGEWORK_PUBLIC_BASE_URL",
        "MERGEWORK_GITHUB_WEBHOOK_SECRET",
        "MERGEWORK_GITHUB_OAUTH_CLIENT_ID",
        "MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET",
        "MERGEWORK_ADMIN_LOGINS",
        "MERGEWORK_GITHUB_ACCEPTED_LABELERS",
        "MERGEWORK_ADMIN_TOKEN",
        "MERGEWORK_COOKIE_SECRET",
    }

    deploy_step = workflow[
        _position(workflow, "MERGEWORK_DATABASE_URL") : _position(
            workflow, "python scripts/check_deploy_ready.py"
        )
    ]

    env_values = {name: _env_value(deploy_step, name) for name in required_env}

    assert env_values["MERGEWORK_DATABASE_URL"].startswith("sqlite:////")
    assert env_values["MERGEWORK_PUBLIC_BASE_URL"].startswith("https://")
    assert len(env_values["MERGEWORK_GITHUB_WEBHOOK_SECRET"]) >= 32
    assert len(env_values["MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET"]) >= 32
    assert len(env_values["MERGEWORK_ADMIN_TOKEN"]) >= 32
    assert len(env_values["MERGEWORK_COOKIE_SECRET"]) >= 32
