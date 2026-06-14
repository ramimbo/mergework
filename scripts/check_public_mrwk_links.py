from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.api_host_args import public_api_host, public_http_url

DEFAULT_WEB_HOST = "https://mrwk.online"
DEFAULT_API_HOST = "https://api.mrwk.online"
PROBE_TIMEOUT_SECONDS = 30
PROBE_SAFETY_CAP = 200

# Public route shapes that MergeWork writes into bounty issue bodies and
# comments. These are the authoritative status/proof surfaces contributors
# follow, so a published link that resolves to an Express 404 shell hides the
# real bounty/proof state. See proposed-work issue #1119.
# Ordered most-specific first so /api/v1/bounties/ is not shadowed by /bounties/.
KNOWN_ROUTE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("treasury_proposal", "/api/v1/treasury/proposals/"),
    ("api_bounty", "/api/v1/bounties/"),
    ("account", "/api/v1/accounts/"),
    ("wallet", "/api/v1/wallets/"),
    ("proof", "/proofs/"),
    ("bounty", "/bounties/"),
)

# An Express "Cannot GET ..." body is a 404 shell even on hosts that answer
# 200 at the apex; treat the body text as authoritative, not just the status.
EXPRESS_NOT_FOUND_MARKERS: tuple[str, ...] = (
    "cannot get ",
    "cannot post ",
)

_URL_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~:/?#[]@!$&'()*+,;=%"
)


def _trim_trailing_punct(url: str) -> str:
    while url and url[-1] in ").,'\"`>]}":
        url = url[:-1]
    return url


def extract_candidate_links(text: str) -> list[str]:
    """Pull http(s) URLs out of free-form issue/PR/comment text, in order and
    de-duplicated. Pure: no network."""
    found: list[str] = []
    seen: set[str] = set()
    haystack = text or ""
    lowered = haystack.lower()
    idx = 0
    while True:
        hit = -1
        for scheme in ("https://", "http://"):
            pos = lowered.find(scheme, idx)
            if pos != -1 and (hit == -1 or pos < hit):
                hit = pos
        if hit == -1:
            break
        end = hit
        while end < len(haystack) and haystack[end] in _URL_CHARS:
            end += 1
        url = _trim_trailing_punct(haystack[hit:end])
        idx = end
        if not url or url in seen:
            continue
        try:
            public_http_url(url)
        except ValueError:
            continue
        seen.add(url)
        found.append(url)
    return found


def classify_link(url: str) -> str | None:
    """Return a known MergeWork public-route kind for a URL, or None when the
    URL is not one of the published status/proof surfaces. Pure."""
    for kind, needle in KNOWN_ROUTE_PATTERNS:
        if needle in url:
            return kind
    return None


def is_express_not_found(body: str) -> bool:
    """True when a response body is an Express 'Cannot GET/POST' 404 shell."""
    head = (body or "").strip().lower()[:64]
    return any(head.startswith(marker) for marker in EXPRESS_NOT_FOUND_MARKERS)


def evaluate_probe(probe: dict[str, Any]) -> dict[str, Any]:
    """Decide whether a single probe result is healthy. Pure.

    A link is unhealthy when the request errored, the status is not 2xx/3xx,
    or the body is an Express 'Cannot GET' shell.
    """
    url = str(probe.get("url") or "")
    kind = probe.get("kind")
    error = probe.get("error")
    status = probe.get("status")
    body = str(probe.get("body") or "")

    if error:
        reason = f"request error: {error}"
        healthy = False
    elif not isinstance(status, int):
        reason = "no status code"
        healthy = False
    elif is_express_not_found(body):
        reason = f"express not-found shell (status {status})"
        healthy = False
    elif 200 <= status < 400:
        reason = f"ok ({status})"
        healthy = True
    else:
        reason = f"non-2xx/3xx status ({status})"
        healthy = False

    return {
        "url": url,
        "kind": kind,
        "status": status,
        "healthy": healthy,
        "reason": reason,
    }


def analyze_link_health(probes: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up probe evaluations into a pass/fail report. Pure."""
    evaluated = [evaluate_probe(p) for p in probes]
    unhealthy = [e for e in evaluated if not e["healthy"]]
    return {
        "checked": len(evaluated),
        "healthy": len(evaluated) - len(unhealthy),
        "unhealthy": unhealthy,
        "results": evaluated,
        "ok": not unhealthy,
    }


def probe_link(url: str, *, timeout: int = PROBE_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Fetch a URL and capture status + a slice of the body. Network."""
    request = urllib.request.Request(
        url, method="GET", headers={"User-Agent": "mergework-link-health"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(2048).decode("utf-8", "replace")
            return {
                "url": url,
                "kind": classify_link(url),
                "status": response.status,
                "body": body,
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(2048).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - body is best-effort
            body = ""
        return {
            "url": url,
            "kind": classify_link(url),
            "status": exc.code,
            "body": body,
            "error": None,
        }
    except (urllib.error.URLError, ValueError, OSError) as exc:
        return {
            "url": url,
            "kind": classify_link(url),
            "status": None,
            "body": "",
            "error": str(exc),
        }


def gather_links(
    *,
    text: str = "",
    urls: list[str] | None = None,
    known_routes_only: bool = False,
) -> list[str]:
    """Combine explicit URLs and URLs extracted from text. Pure."""
    collected: list[str] = []
    seen: set[str] = set()
    for url in list(urls or []) + extract_candidate_links(text):
        if url in seen:
            continue
        if known_routes_only and classify_link(url) is None:
            continue
        seen.add(url)
        collected.append(url)
    return collected


def run_checks(
    urls: list[str], *, timeout: int = PROBE_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Probe each URL (capped) and analyze. Network."""
    probes = [probe_link(url, timeout=timeout) for url in urls[:PROBE_SAFETY_CAP]]
    return analyze_link_health(probes)


def _read_text_inputs(paths: list[str]) -> str:
    chunks: list[str] = []
    for raw in paths:
        if raw == "-":
            chunks.append(sys.stdin.read())
            continue
        chunks.append(Path(raw).read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate that the public mrwk.online / api.mrwk.online links "
            "MergeWork publishes in bounty issues and comments resolve to real "
            "status/proof views instead of Express 404 shells (proposed-work #1119)."
        )
    )
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        metavar="URL",
        help="Explicit public link to check (repeatable).",
    )
    parser.add_argument(
        "--text-file",
        action="append",
        default=[],
        metavar="PATH",
        help="File with issue/PR/comment text to scan for links ('-' for stdin, repeatable).",
    )
    parser.add_argument(
        "--web-host",
        type=public_http_url,
        default=DEFAULT_WEB_HOST,
        help=f"Public web host (default {DEFAULT_WEB_HOST}).",
    )
    parser.add_argument(
        "--api-host",
        type=public_api_host,
        default=DEFAULT_API_HOST,
        help=f"Public API host (default {DEFAULT_API_HOST}).",
    )
    parser.add_argument(
        "--known-routes-only",
        action="store_true",
        help="Only check URLs matching a known MergeWork public route.",
    )
    parser.add_argument(
        "--sample-hosts",
        action="store_true",
        help="If no links are supplied, sample representative routes on the configured hosts.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=PROBE_TIMEOUT_SECONDS,
        help="Per-request timeout seconds.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    return parser


def _sample_routes(web_host: str, api_host: str) -> list[str]:
    web = web_host.rstrip("/")
    api = api_host.rstrip("/")
    return [f"{web}/", f"{api}/api/v1/health", f"{api}/api/v1/bounties"]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    text = _read_text_inputs(args.text_file) if args.text_file else ""
    urls = gather_links(
        text=text, urls=args.url, known_routes_only=args.known_routes_only
    )
    if not urls and args.sample_hosts:
        urls = _sample_routes(args.web_host, args.api_host)

    if not urls:
        message = "no links to check (pass --url, --text-file, or --sample-hosts)"
        if args.json:
            print(json.dumps({"checked": 0, "ok": True, "note": message}))
        else:
            print(message)
        return 0

    report = run_checks(urls, timeout=args.timeout)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"checked {report['checked']} link(s); healthy {report['healthy']}")
        for result in report["unhealthy"]:
            print(f"  UNHEALTHY {result['url']} -> {result['reason']}")
        if report["ok"]:
            print("all published links resolve to live views")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
