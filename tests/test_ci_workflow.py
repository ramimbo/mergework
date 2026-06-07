from __future__ import annotations

from pathlib import Path


def test_ci_runs_live_bounty_closing_reference_check() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "python scripts/check_live_bounty_closing_refs.py" in workflow
    assert "if: github.event_name == 'pull_request'" in workflow
    assert "GH_TOKEN: ${{ github.token }}" in workflow
    assert "--repo ramimbo/mergework" in workflow
    assert "--pr ${{ github.event.pull_request.number }}" in workflow
    assert "--fail-on-issues" in workflow
