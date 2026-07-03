from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REVIEW_ROUND_TITLE_RE = re.compile(
    r"review open MergeWork PRs with evidence,\s*round\s+(\d+)",
    re.IGNORECASE,
)
REQUIRED_LABELS = {"mrwk:bounty", "review"}


def _labels(raw: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for label in raw.get("labels", []):
        if isinstance(label, str):
            names.add(label)
        elif isinstance(label, dict) and isinstance(label.get("name"), str):
            names.add(label["name"])
    return names


def _issue_number(raw: dict[str, Any]) -> int | None:
    value = raw.get("number", raw.get("issue_number"))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _round_number(raw: dict[str, Any]) -> int | None:
    title = str(raw.get("title") or "")
    match = REVIEW_ROUND_TITLE_RE.search(title)
    if not match:
        return None
    return int(match.group(1))


def is_review_bounty_issue(raw: dict[str, Any]) -> bool:
    labels = _labels(raw)
    if not REQUIRED_LABELS.issubset(labels):
        return False
    return _round_number(raw) is not None


def classify_review_rounds(issues: list[dict[str, Any]]) -> dict[str, Any]:
    review_issues = [issue for issue in issues if is_review_bounty_issue(issue)]
    if not review_issues:
        return {
            "current_issue_number": None,
            "current_round": None,
            "superseded": [],
            "open_review_rounds": [],
        }

    numbered = [
        (issue, _round_number(issue)) for issue in review_issues if _round_number(issue) is not None
    ]
    numbered.sort(key=lambda item: (item[1] or 0, _issue_number(item[0]) or 0))
    open_numbered = [item for item in numbered if str(item[0].get("state") or "").lower() == "open"]
    if not open_numbered:
        return {
            "current_issue_number": None,
            "current_round": None,
            "superseded": [],
            "open_review_rounds": [],
        }
    current_issue, current_round = open_numbered[-1]
    current_number = _issue_number(current_issue)

    superseded: list[dict[str, Any]] = []
    open_review_rounds: list[dict[str, Any]] = []
    for issue, round_no in open_numbered:
        number = _issue_number(issue)
        if number is None or round_no is None:
            continue
        row = {
            "issue_number": number,
            "round": round_no,
            "title": str(issue.get("title") or ""),
            "state": str(issue.get("state") or "unknown"),
            "labels": sorted(_labels(issue)),
        }
        open_review_rounds.append(row)
        if number != current_number:
            superseded.append(
                {
                    **row,
                    "reason": (
                        f"round {round_no} is older than current live round {current_round}"
                    ),
                    "recommended_action": (
                        f"close, label, or comment pointing reviewers to #{current_number}"
                    ),
                }
            )

    return {
        "current_issue_number": current_number,
        "current_round": current_round,
        "superseded": superseded,
        "open_review_rounds": open_review_rounds,
    }


def render_report(report: dict[str, Any]) -> str:
    lines = ["Superseded review bounty round report", ""]
    current = report.get("current_issue_number")
    current_round = report.get("current_round")
    if current is None:
        lines.append("No open mrwk:bounty + review round issues found.")
        return "\n".join(lines)

    lines.append(f"Current live review round: #{current} (round {current_round})")
    lines.append("")
    superseded = report.get("superseded") or []
    if not superseded:
        lines.append("No superseded open review rounds detected.")
        return "\n".join(lines)

    lines.append("Likely superseded open review rounds:")
    for row in superseded:
        lines.append(
            f"- #{row['issue_number']} round {row['round']} ({row['state']}): {row['reason']}",
        )
        lines.append(f"  recommended: {row['recommended_action']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Identify superseded open review bounty rounds from issue metadata.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="JSON file with top-level 'issues' list (GitHub issue objects).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of a human-readable report.",
    )
    parser.add_argument(
        "--fail-on-superseded",
        action="store_true",
        help="Exit 1 when any superseded open review round is found.",
    )
    args = parser.parse_args(argv)

    if args.input is None:
        print("flag_superseded_review_rounds: --input is required", file=sys.stderr)
        return 2

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    issues = payload.get("issues", payload if isinstance(payload, list) else [])
    if not isinstance(issues, list):
        print("flag_superseded_review_rounds: expected issues list", file=sys.stderr)
        return 2

    report = classify_review_rounds(issues)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_report(report))

    if args.fail_on_superseded and report.get("superseded"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
