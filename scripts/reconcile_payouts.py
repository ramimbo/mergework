from __future__ import annotations

import json
from dataclasses import asdict

from app.config import get_settings
from app.db import session_scope
from app.ledger.reconciliation import (
    duplicate_accepted_source_urls,
    duplicate_source_summary,
    payout_reconciliation_summary,
    reconcile_accepted_payouts,
)


def main() -> int:
    try:
        settings = get_settings()
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "summary": {"status": "configuration_invalid"},
                    "issues": [{"status": "configuration_invalid", "message": str(exc)}],
                    "duplicate_source_urls": [],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    with session_scope(settings.database_url) as session:
        checks = reconcile_accepted_payouts(session)
        duplicate_sources = duplicate_accepted_source_urls(session)
    summary = payout_reconciliation_summary(checks)
    summary.update(duplicate_source_summary(duplicate_sources))
    issues = [asdict(check) for check in checks if check.status != "paid"]
    duplicate_source_urls = [asdict(group) for group in duplicate_sources]
    print(
        json.dumps(
            {
                "summary": summary,
                "issues": issues,
                "duplicate_source_urls": duplicate_source_urls,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if issues or duplicate_source_urls else 0


if __name__ == "__main__":
    raise SystemExit(main())
