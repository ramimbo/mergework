from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EXPRESS_CANNOT_GET = "Cannot GET"
DEFAULT_USER_AGENT = "mergework-public-link-health-check"
GH_TIMEOUT_SECONDS = 30


def is_healthy_oauth_route(status_code: int | None, body: str) -> bool:
    if status_code is None:
        return False
    if EXPRESS_CANNOT_GET in body:
        return False
    if status_code == 404:
        return False
    return status_code in {200, 302, 422, 503}


def is_healthy_link(status_code: int | None, body: str, *, link_type: str = "unknown") -> bool:
    if link_type == "oauth":
        return is_healthy_oauth_route(status_code, body)
    if status_code is None or status_code < 200 or status_code >= 400:
        return False
    return EXPRESS_CANNOT_GET not in body


def analyze_probe_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    for row in rows:
        url = str(row.get("url") or "")
        status_code = row.get("status_code")
        body = str(row.get("body") or "")
        link_type = str(row.get("type") or "unknown")
        if is_healthy_link(status_code, body, link_type=link_type):
            continue
        detail = f"{link_type} link unhealthy: HTTP {status_code}"
        if EXPRESS_CANNOT_GET in body:
            detail += " (Express Cannot GET shell)"
        violations.append(
            {
                "url": url,
                "type": link_type,
                "status_code": status_code,
                "detail": detail,
                "source": row.get("source"),
            }
        )
    return {
        "summary": {
            "checked_links": len(rows),
            "unhealthy_links": len(violations),
        },
        "violations": violations,
    }


def probe_url(url: str, *, timeout: float = GH_TIMEOUT_SECONDS) -> dict[str, Any]:
    if not url.lower().startswith(("http://", "https://")):
        return {
            "url": url,
            "status_code": None,
            "body": "unsupported URL scheme (http/https only)",
        }
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "*/*",
            "User-Agent": DEFAULT_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
            return {
                "url": url,
                "status_code": response.status,
                "body": body,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        return {
            "url": url,
            "status_code": exc.code,
            "body": body,
        }
    except urllib.error.URLError as exc:
        return {
            "url": url,
            "status_code": None,
            "body": str(exc.reason or exc),
        }


def load_input_rows(path: Path) -> list[dict[str, Any]]:
    """Load URL targets from a fixture. Precomputed status/body fields are ignored."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        raw_rows = cast(list[dict[str, Any]], payload)
    elif isinstance(payload, dict) and isinstance(payload.get("links"), list):
        raw_rows = cast(list[dict[str, Any]], payload["links"])
    else:
        raise ValueError("Input JSON must be a list of link probes or an object with a links array")
    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        rows.append(
            {
                "url": url,
                "type": str(item.get("type") or "unknown"),
                "source": str(item.get("source") or path.name),
            }
        )
    return rows


def format_report(report: dict[str, Any], *, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(report, indent=2, sort_keys=True)
    lines = [
        "Public MRWK link health check",
        f"- checked: {report['summary']['checked_links']}",
        f"- unhealthy: {report['summary']['unhealthy_links']}",
    ]
    for violation in report["violations"]:
        lines.append(f"- {violation['detail']}: {violation['url']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate public MRWK bounty/proposal/proof links.",
    )
    parser.add_argument("--input", type=Path, help="JSON fixture with link URL targets")
    parser.add_argument("--url", action="append", default=[], help="Live URL to probe")
    parser.add_argument(
        "--type",
        default="unknown",
        help="Default link type label for --url probes",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--fail-on-issues", action="store_true")
    args = parser.parse_args(argv)

    rows: list[dict[str, Any]] = []
    if args.input:
        for target in load_input_rows(args.input):
            probed = probe_url(str(target["url"]))
            probed["type"] = target.get("type") or "unknown"
            probed["source"] = target.get("source") or args.input.name
            rows.append(probed)
    for url in args.url:
        row = probe_url(url)
        row["type"] = args.type
        row["source"] = "--url"
        rows.append(row)

    if not rows:
        parser.error("Provide --input or at least one --url")

    report = analyze_probe_results(rows)
    print(format_report(report, fmt=args.format))
    if args.fail_on_issues and report["summary"]["unhealthy_links"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
