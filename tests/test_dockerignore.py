from __future__ import annotations

from pathlib import Path


def test_dockerignore_excludes_local_env_variants() -> None:
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in dockerignore
    assert ".env.*" in dockerignore
    assert "!.env.example" in dockerignore
