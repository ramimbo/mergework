from __future__ import annotations

import argparse
import json
import subprocess

import pytest

from scripts import api_host_args
from scripts.api_host_args import public_api_host, public_http_url, run_readonly_gh_json


def test_public_http_url_rejects_blank_and_relative_values() -> None:
    with pytest.raises(ValueError, match=r"service URL must be a non-empty HTTP\(S\) URL"):
        public_http_url("   ", label="service URL")
    with pytest.raises(ValueError, match=r"service URL must be an absolute HTTP\(S\) URL"):
        public_http_url("/api/v1/bounties", label="service URL")


def test_public_http_url_can_reject_embedded_credentials() -> None:
    with pytest.raises(ValueError, match="service URL must not include username or password"):
        public_http_url(
            "https://operator:secret@staging.mrwk.example.test",
            label="service URL",
            forbid_credentials=True,
        )

    assert public_http_url("https://staging.mrwk.example.test", label="service URL") == (
        "https://staging.mrwk.example.test"
    )


def test_public_api_host_preserves_argparse_error_type() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="api host must be an absolute"):
        public_api_host("localhost:8000")


def test_run_readonly_gh_json_decodes_utf8_stdout(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"ok": True}),
            stderr="",
        )

    monkeypatch.setattr(api_host_args.subprocess, "run", fake_run)

    assert run_readonly_gh_json(["gh", "issue", "list"], timeout_seconds=30) == {"ok": True}


def test_run_readonly_gh_json_reports_missing_gh(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(api_host_args.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="GitHub CLI executable 'gh' was not found"):
        run_readonly_gh_json(["gh", "issue", "list"], timeout_seconds=30)
