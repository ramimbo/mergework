from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any

PROPOSED_WORK_LABEL = "proposed-work"
GH_TIMEOUT_SECONDS = 30
GH_ISSUE_SAFETY_CAP = 201
PROPOSED_WORK_TITLE_RE = re.compile(r"^\s*proposed\s+work\s*:", re.IGNORECASE)
READ_ONLY_ISSUE_COMMANDS = {"list", "view"}
READ_ONLY_API_METHODS = {"GET", "HEAD"}

REQUIRED_SECTION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("problem", ("problem",)),
    ("evidence", ("evidence",)),
    ("proposed_work", ("proposed work",)),
    ("expected_value", ("expected value",)),
    ("acceptance_criteria", ("possible acceptance criteria", "acceptance criteria")),
    ("tests", ("evidence or tests required", "tests required")),
    ("duplicate_search", ("duplicate search",)),
    ("out_of_scope", ("out of scope",)),
)


@dataclass(frozen=True)
class ProposedWorkRow:
    issue_number: int
    title: str
    url: str | None
    author: str
    detection: str
    missing_sections: list[str]
    warnings: list[str]


def _label_names(raw: dict[str, Any]) -> list[str]:
    labels = raw.get("labels", [])
    names: list[str] = []
    if not isinstance(labels, list):
        return names
    for label in labels:
        if isinstance(label, str):
            names.append(label)
        elif isinstance(label, dict) and isinstance(label.get("name"), str):
            names.append(label["name"])
    return names


def _author_login(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        login = raw.get("login")
        if isinstance(login, str) and login:
            return login
    return "unknown"


def _normalize_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _headings(body: str) -> set[str]:
    headings: set[str] = set()
    for match in re.finditer(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", body or "", re.MULTILINE):
        headings.add(_normalize_heading(match.group(1)))
    return headings


def _missing_sections(body: str) -> list[str]:
    headings = _headings(body)
    missing: list[str] = []
    for key, aliases in REQUIRED_SECTION_GROUPS:
        if not any(alias in headings for alias in aliases):
            missing.append(key)
    return missing


def _is_labeled_proposed_work(issue: dict[str, Any]) -> bool:
    return any(label.lower() == PROPOSED_WORK_LABEL for label in _label_names(issue))


def _has_proposed_work_title(issue: dict[str, Any]) -> bool:
    return bool(PROPOSED_WORK_TITLE_RE.search(str(issue.get("title") or "")))


def _detect_issue(issue: dict[str, Any]) -> ProposedWorkRow | None:
    number = issue.get("number")
    if not isinstance(number, int):
        return None
    title = str(issue.get("title") or "")
    body = str(issue.get("body") or "")
    labeled = _is_labeled_proposed_work(issue)
    title_matches = _has_proposed_work_title(issue)
    missing_sections = _missing_sections(body)
    body_matches_template = title_matches and not missing_sections
    if not labeled and not body_matches_template:
        return None

    warnings: list[str] = []
    if not labeled:
        warnings.append("missing_proposed_work_label")
    if missing_sections:
        warnings.append("missing_required_sections")

    if labeled and body_matches_template:
        detection = "label_and_template"
    elif labeled:
        detection = "label"
    else:
        detection = "title_body_fallback"

    return ProposedWorkRow(
        issue_number=number,
        title=title,
        url=issue.get("url") if isinstance(issue.get("url"), str) else None,
        author=_author_login(issue.get("author")),
        detection=detection,
        missing_sections=missing_sections,
        warnings=warnings,
    )


def analyze_queue(data: dict[str, Any]) -> dict[str, Any]:
    rows: list[ProposedWorkRow] = []
    ignored_proposed_titles = 0
    issues_seen = 0
    for issue in data.get("issues", []):
        if not isinstance(issue, dict):
            continue
        issues_seen += 1
        row = _detect_issue(issue)
        if row is None:
            if _has_proposed_work_title(issue):
                ignored_proposed_titles += 1
            continue
        rows.append(row)

    rows.sort(key=lambda item: item.issue_number)
    fallback_rows = [row for row in rows if row.detection == "title_body_fallback"]
    missing_section_rows = [row for row in rows if row.missing_sections]
    return {
        "summary": {
            "issues_seen": issues_seen,
            "proposed_work": len(rows),
            "labeled": len([row for row in rows if "label" in row.detection]),
            "title_body_fallback": len(fallback_rows),
            "missing_label": len(fallback_rows),
            "missing_required_sections": len(missing_section_rows),
            "ignored_proposed_titles": ignored_proposed_titles,
        },
        "required_sections": [key for key, _aliases in REQUIRED_SECTION_GROUPS],
        "rows": [asdict(row) for row in rows],
    }


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def format_text_report(report: dict[str, Any]) -> str:
    lines = ["Proposed-work queue summary"]
    for key, value in report["summary"].items():
        lines.append(f"- {key.replace('_', ' ')}: {value}")
    if not report["rows"]:
        lines.append("")
        lines.append("No proposed-work intake rows found.")
        return "\n".join(lines)
    lines.append("")
    lines.append("Rows")
    for row in report["rows"]:
        warnings = ", ".join(row["warnings"]) if row["warnings"] else "none"
        lines.append(
            f"- Issue #{row['issue_number']}: {_single_line(row['title'])} "
            f"({row['detection']}; warnings: {warnings})"
        )
    return "\n".join(lines)


def format_markdown_report(report: dict[str, Any]) -> str:
    lines = ["## Proposed-Work Queue", ""]
    for key, value in report["summary"].items():
        lines.append(f"- **{key.replace('_', ' ')}**: {value}")
    lines.append("")
    lines.append("| Issue | Detection | Author | Warnings | Missing sections |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in report["rows"]:
        issue = f"#{row['issue_number']}"
        if row.get("url"):
            issue = f"[{issue}]({row['url']})"
        warnings = ", ".join(row["warnings"]) if row["warnings"] else ""
        missing = ", ".join(row["missing_sections"]) if row["missing_sections"] else ""
        lines.append(
            f"| {issue} | `{row['detection']}` | {row['author']} | {warnings} | {missing} |"
        )
    return "\n".join(lines)


def _run_gh_json(args: list[str]) -> Any:
    if len(args) < 2 or args[0] != "gh":
        raise RuntimeError(f"refusing non-gh command: {' '.join(args)}")
    if args[1] == "issue":
        command = args[2] if len(args) > 2 else ""
        if command not in READ_ONLY_ISSUE_COMMANDS:
            raise RuntimeError(f"refusing non-read-only gh issue command: {' '.join(args)}")
    elif args[1] == "api":
        method = None
        for flag in ("--method", "-X"):
            if flag not in args:
                continue
            index = args.index(flag)
            method = args[index + 1].upper() if index + 1 < len(args) else ""
            break
        if method not in READ_ONLY_API_METHODS:
            raise RuntimeError(f"refusing non-read-only gh api command: {' '.join(args)}")
    else:
        raise RuntimeError(f"refusing non-read-only gh issue command: {' '.join(args)}")
    try:
        completed = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GH_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "gh executable not found; install GitHub CLI to use --repo mode"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"gh command timed out after {GH_TIMEOUT_SECONDS}s") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"gh command failed with exit {exc.returncode}: {' '.join(args)}\n{exc.stderr}"
        ) from exc
    return json.loads(completed.stdout)


def load_live_queue(repo: str) -> dict[str, Any]:
    issues = _run_gh_json(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            str(GH_ISSUE_SAFETY_CAP),
            "--json",
            "number,title,url,body,labels,author",
        ]
    )
    if len(issues) >= GH_ISSUE_SAFETY_CAP:
        raise RuntimeError(
            f"gh issue list reached the {GH_ISSUE_SAFETY_CAP} item safety cap; "
            "use an API-paginated collector before trusting this live report"
        )
    return {"issues": issues}


def _load_input(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("proposed-work queue input must be a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize MergeWork proposed-work intake.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Read queue data from a JSON fixture file.")
    source.add_argument(
        "--repo",
        help="Collect live proposed-work data with gh, for example ramimbo/mergework.",
    )
    parser.add_argument("--format", choices=["json", "markdown", "text"], default="text")
    args = parser.parse_args(argv)

    data = _load_input(args.input) if args.input else load_live_queue(args.repo)
    report = analyze_queue(data)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.format == "markdown":
        print(format_markdown_report(report))
    else:
        print(format_text_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
