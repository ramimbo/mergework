from __future__ import annotations

import json
from dataclasses import asdict

from app.config import get_settings
from app.db import session_scope
from app.ledger.reconciliation import (
    payout_reconciliation_summary,
    reconcile_accepted_payouts,
)


def main() -> int:
    settings = get_settings()
    with session_scope(settings.database_url) as session:
        checks = reconcile_accepted_payouts(session)
    summary = payout_reconciliation_summary(checks)
    issues = [asdict(check) for check in checks if check.status != "paid"]
    print(json.dumps({"summary": summary, "issues": issues}, indent=2, sort_keys=True))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
