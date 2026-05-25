from __future__ import annotations

import json
from dataclasses import asdict

from app.config import get_settings
from app.db import session_scope
from app.ledger.reconciliation import (
    exhausted_round_overflow_detection,
    overflow_summary,
    payout_reconciliation_summary,
    reconcile_accepted_payouts,
)


def main() -> int:
    settings = get_settings()
    with session_scope(settings.database_url) as session:
        checks = reconcile_accepted_payouts(session)
        overflows = exhausted_round_overflow_detection(session)
    summary = payout_reconciliation_summary(checks)
    overflow_summary_data = overflow_summary(overflows)
    issues = [asdict(check) for check in checks if check.status != "paid"]
    result = {
        "summary": summary,
        "overflow_summary": overflow_summary_data,
        "issues": issues,
        "overflows": [asdict(ov) for ov in overflows],
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if issues or overflow_summary_data["exhausted_rounds_with_overflow"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
