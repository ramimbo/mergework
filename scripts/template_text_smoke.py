from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_TEMPLATE_DIR = ROOT / "app" / "templates"

# Typographic / "smart" quote characters that should not wrap dynamic public
# notice text. The recurring activity/wallet/bounty search-notice fixes
# (for example PR #1052 and PR #1110) all replaced these with plain ASCII
# quotes so the rendered notice stays readable across browsers and terminals.
SMART_QUOTES: tuple[str, ...] = (
    "“",  # left double quotation mark
    "”",  # right double quotation mark
    "‘",  # left single quotation mark
    "’",  # right single quotation mark
    "„",  # double low-9 quotation mark
    "‚",  # single low-9 quotation mark
    "″",  # double prime
    "′",  # single prime
)

# U+FFFD marks a decode failure; it is a reliable mojibake signal.
REPLACEMENT_CHAR = "�"

# Common "UTF-8 bytes decoded as Latin-1" mojibake sequences for the same
# curly quotes/dashes above. These show up when notice text round-trips
# through a mismatched encoding before it reaches the page.
MOJIBAKE_SEQUENCES: tuple[str, ...] = (
    "â€œ",  # curly left double quote
    "â€",  # curly right double quote
    "â€™",  # curly apostrophe
    "â€˜",  # curly left single quote
    "â€“",  # en dash
)

# Jinja delimiters that must never survive into rendered output a reader sees.
RAW_PLACEHOLDER_OPEN = "{{"
RAW_PLACEHOLDER_CLOSE = "}}"

# A line carrying this marker is intentionally exempt (docs/code examples or
# deliberately non-ASCII content). Keeps the rule bounded, per the issue.
ALLOW_MARKER = "template-text-smoke: allow"


@dataclass(frozen=True)
class Finding:
    source: str
    line: int
    kind: str
    detail: str

    def describe(self) -> str:
        return f"{self.source}:{self.line}: {self.kind}: {self.detail}"


def scan_text(text: str, *, source: str = "<text>", allow_jinja: bool = True) -> list[Finding]:
    """Return rendering-hazard findings for ``text``.

    ``allow_jinja=True`` treats the input as template source, where ``{{ ... }}``
    placeholders are expected. ``allow_jinja=False`` treats the input as rendered
    output, where a surviving ``{{ ... }}`` placeholder is a leak.
    """

    findings: list[Finding] = []
    for index, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        for quote in SMART_QUOTES:
            if quote in line:
                findings.append(
                    Finding(source, index, "smart-quote", f"typographic quote {quote!r}")
                )
                break
        if REPLACEMENT_CHAR in line:
            findings.append(
                Finding(source, index, "replacement-char", "U+FFFD replacement character")
            )
        for sequence in MOJIBAKE_SEQUENCES:
            if sequence in line:
                findings.append(
                    Finding(source, index, "mojibake", f"mojibake sequence {sequence!r}")
                )
                break
        if not allow_jinja and RAW_PLACEHOLDER_OPEN in line and RAW_PLACEHOLDER_CLOSE in line:
            findings.append(
                Finding(source, index, "raw-placeholder", "raw Jinja placeholder in rendered text")
            )
    return findings


def _display(path: Path) -> str:
    """Show a repo-relative path when possible, otherwise the given path."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def iter_public_templates() -> list[Path]:
    return sorted(PUBLIC_TEMPLATE_DIR.glob("*.html"))


def scan_template_file(path: Path) -> list[Finding]:
    return scan_text(path.read_text(encoding="utf-8"), source=_display(path), allow_jinja=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-check public template text for rendering hazards.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files to scan. Defaults to app/templates/*.html.",
    )
    parser.add_argument(
        "--rendered",
        action="store_true",
        help="Treat input as rendered HTML and also flag raw {{ }} placeholder leakage.",
    )
    args = parser.parse_args(argv)

    targets = args.paths or iter_public_templates()
    findings: list[Finding] = []
    for path in targets:
        if not path.exists():
            print(f"missing: {path}")
            return 1
        text = path.read_text(encoding="utf-8")
        findings.extend(scan_text(text, source=_display(path), allow_jinja=not args.rendered))

    for finding in findings:
        print(finding.describe())
    if findings:
        print(f"template text smoke found {len(findings)} hazard(s)")
        return 1
    print("template text smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
