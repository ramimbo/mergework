from __future__ import annotations

from scripts.check_public_mrwk_links import (
    analyze_link_health,
    classify_link,
    evaluate_probe,
    extract_candidate_links,
    gather_links,
    is_express_not_found,
)


def test_extract_candidate_links_finds_published_routes() -> None:
    text = (
        "Reserved on MergeWork. Public row: https://mrwk.online/bounties/120 .\n"
        "Proposal https://api.mrwk.online/api/v1/treasury/proposals/211, and\n"
        "proof (https://mrwk.online/proofs/318f54e8). Duplicate: https://mrwk.online/bounties/120"
    )
    links = extract_candidate_links(text)
    assert links == [
        "https://mrwk.online/bounties/120",
        "https://api.mrwk.online/api/v1/treasury/proposals/211",
        "https://mrwk.online/proofs/318f54e8",
    ]


def test_extract_ignores_non_http_tokens() -> None:
    assert extract_candidate_links("see github:jakerated-r and mailto:x@y.z") == []


def test_classify_link_known_and_unknown() -> None:
    assert classify_link("https://mrwk.online/bounties/120") == "bounty"
    assert (
        classify_link("https://api.mrwk.online/api/v1/treasury/proposals/211")
        == "treasury_proposal"
    )
    assert classify_link("https://mrwk.online/proofs/deadbeef") == "proof"
    assert classify_link("https://api.mrwk.online/api/v1/bounties/117") == "api_bounty"
    assert classify_link("https://example.com/somewhere") is None


def test_is_express_not_found_detects_shell() -> None:
    assert is_express_not_found("Cannot GET /bounties/120") is True
    assert is_express_not_found("  cannot post /proofs/x ") is True
    assert is_express_not_found('{"id": 117, "status": "open"}') is False
    assert is_express_not_found("") is False


def test_evaluate_probe_flags_express_shell_even_on_200() -> None:
    result = evaluate_probe(
        {
            "url": "https://mrwk.online/bounties/120",
            "kind": "bounty",
            "status": 200,
            "body": "Cannot GET /bounties/120",
        }
    )
    assert result["healthy"] is False
    assert "express not-found" in result["reason"]


def test_evaluate_probe_flags_404() -> None:
    result = evaluate_probe(
        {
            "url": "https://api.mrwk.online/api/v1/treasury/proposals/211",
            "status": 404,
            "body": "Cannot GET /...",
        }
    )
    assert result["healthy"] is False


def test_evaluate_probe_passes_real_json_view() -> None:
    result = evaluate_probe(
        {
            "url": "https://api.mrwk.online/api/v1/bounties/117",
            "status": 200,
            "body": '{"id": 117}',
        }
    )
    assert result["healthy"] is True
    assert result["reason"] == "ok (200)"


def test_evaluate_probe_flags_network_error() -> None:
    result = evaluate_probe(
        {"url": "https://mrwk.online/proofs/x", "status": None, "error": "timed out"}
    )
    assert result["healthy"] is False
    assert "request error" in result["reason"]


def test_analyze_link_health_reports_pass_and_fail() -> None:
    probes = [
        {
            "url": "https://api.mrwk.online/api/v1/bounties/117",
            "status": 200,
            "body": '{"id":117}',
        },
        {
            "url": "https://mrwk.online/bounties/120",
            "status": 200,
            "body": "Cannot GET /bounties/120",
        },
        {
            "url": "https://api.mrwk.online/api/v1/treasury/proposals/211",
            "status": 404,
            "body": "Cannot GET /...",
        },
    ]
    report = analyze_link_health(probes)
    assert report["checked"] == 3
    assert report["healthy"] == 1
    assert report["ok"] is False
    assert {u["url"] for u in report["unhealthy"]} == {
        "https://mrwk.online/bounties/120",
        "https://api.mrwk.online/api/v1/treasury/proposals/211",
    }


def test_analyze_link_health_all_healthy_is_ok() -> None:
    probes = [
        {
            "url": "https://api.mrwk.online/api/v1/bounties/117",
            "status": 200,
            "body": "{}",
        }
    ]
    report = analyze_link_health(probes)
    assert report["ok"] is True
    assert report["unhealthy"] == []


def test_gather_links_dedupes_and_can_filter_known_routes() -> None:
    text = "https://mrwk.online/bounties/120 and https://example.com/blog"
    both = gather_links(text=text, urls=["https://api.mrwk.online/api/v1/bounties/117"])
    assert both == [
        "https://api.mrwk.online/api/v1/bounties/117",
        "https://mrwk.online/bounties/120",
        "https://example.com/blog",
    ]
    known_only = gather_links(text=text, known_routes_only=True)
    assert known_only == ["https://mrwk.online/bounties/120"]
