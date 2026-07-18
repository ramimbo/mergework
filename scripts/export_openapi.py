#!/usr/bin/env python3
"""Export the FastAPI OpenAPI document to docs/openapi.yaml."""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "openapi.yaml",
        help="YAML output path (default: docs/openapi.yaml)",
    )
    args = parser.parse_args()

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("PyYAML is required: pip install pyyaml") from exc

    from app.main import create_app

    spec = create_app().openapi()
    spec["servers"] = [
        {"url": "https://api.mrwk.online", "description": "Production API"},
        {
            "url": "https://api.mrwk.ltclab.site",
            "description": "Legacy-compatible API host",
        },
        {"url": "http://localhost:8000", "description": "Local development"},
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    print(f"Wrote {args.output} ({len(spec.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
