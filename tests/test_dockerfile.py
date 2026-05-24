from __future__ import annotations

from pathlib import Path


def _dockerfile() -> str:
    return Path("Dockerfile").read_text(encoding="utf-8")


def _position(text: str, snippet: str) -> int:
    position = text.find(snippet)
    assert position != -1, f"missing Dockerfile snippet: {snippet}"
    return position


def test_dockerfile_preserves_persistent_runtime_database_path() -> None:
    dockerfile = _dockerfile()

    assert (
        "ENV MERGEWORK_DATABASE_URL=sqlite:////srv/mergework/data/mergework.sqlite3" in dockerfile
    )
    assert "mkdir -p /srv/mergework/data" in dockerfile
    assert "chown -R mergework:mergework /srv/mergework" in dockerfile

    assert _position(dockerfile, "mkdir -p /srv/mergework/data") < _position(
        dockerfile, "USER mergework"
    )


def test_dockerfile_runs_readiness_gate_before_starting_app() -> None:
    dockerfile = _dockerfile()

    assert "USER mergework" in dockerfile
    assert "EXPOSE 8000" in dockerfile
    assert '"python scripts/check_deploy_ready.py && uvicorn app.main:app' in dockerfile

    assert _position(dockerfile, "python scripts/check_deploy_ready.py") < _position(
        dockerfile, "uvicorn app.main:app"
    )
