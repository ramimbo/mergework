from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import check_public_mrwk_links
from scripts.check_public_mrwk_links import analyze_probe_results, is_healthy_link, main

ROOT = Path(__file__).resolve().parents[1]


def test_analyze_probe_results_flags_express_cannot_get() -> None:
    report = analyze_probe_results(
        [
            {
                "url": "https://mrwk.online/bounties/120",
                "type": "bounty",
                "status_code": 404,
                "body": "Cannot GET /bounties/120",
            },
            {
                "url": "https://api.mrwk.online/api/v1/treasury/proposals/211",
                "type": "proposal",
                "status_code": 404,
                "body": "Cannot GET /api/v1/treasury/proposals/211",
            },
            {
                "url": "https://mrwk.online/proofs/abc123",
                "type": "proof",
                "status_code": 200,
                "body": '{"hash":"abc123","kind":"bounty_payment"}',
            },
        ]
    )

    assert report["summary"] == {"checked_links": 3, "unhealthy_links": 2}
    assert [item["type"] for item in report["violations"]] == ["bounty", "proposal"]
    assert all("Cannot GET" in item["detail"] for item in report["violations"])


def test_is_healthy_link_accepts_redirect_ready_responses() -> None:
    assert is_healthy_link(200, '{"status":"open"}')
    assert is_healthy_link(302, "")
    assert not is_healthy_link(404, "Cannot GET /proofs/x")
    assert not is_healthy_link(None, "timed out")
    assert is_healthy_link(422, '{"detail":[]}', link_type="oauth")
    assert not is_healthy_link(422, '{"detail":[]}', link_type="bounty")


def test_check_public_mrwk_links_cli_live_probes_input(tmp_path, capsys, monkeypatch) -> None:
    fixture = {
        "links": [
            {
                "url": "https://mrwk.online/bounties/120",
                "type": "bounty",
            }
        ]
    }
    input_path = tmp_path / "links.json"
    input_path.write_text(json.dumps(fixture), encoding="utf-8")

    def fake_probe(url: str, *, timeout: float = 30) -> dict:
        assert url == "https://mrwk.online/bounties/120"
        return {"url": url, "status_code": 200, "body": '{"id":120,"status":"open"}'}

    monkeypatch.setattr(check_public_mrwk_links, "probe_url", fake_probe)
    exit_code = main(["--input", str(input_path), "--format", "text"])
    assert exit_code == 0
    assert "unhealthy: 0" in capsys.readouterr().out


def test_check_public_mrwk_links_fail_on_issues(tmp_path, monkeypatch) -> None:
    fixture = {"links": [{"url": "https://mrwk.online/bounties/missing", "type": "bounty"}]}
    input_path = tmp_path / "links.json"
    input_path.write_text(json.dumps(fixture), encoding="utf-8")

    monkeypatch.setattr(
        check_public_mrwk_links,
        "probe_url",
        lambda url, *, timeout=30: {
            "url": url,
            "status_code": 404,
            "body": "Cannot GET /bounties/missing",
        },
    )
    assert main(["--input", str(input_path), "--fail-on-issues"]) == 1


def test_check_public_mrwk_links_script_entrypoint() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_public_mrwk_links.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--fail-on-issues" in result.stdout
