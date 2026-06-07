from __future__ import annotations

import argparse

import pytest

from scripts.api_host_args import public_api_host, public_http_url


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
