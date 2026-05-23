from __future__ import annotations

import os

from app.db import session_scope
from app.ledger.service import reconcile_accepted_work_payouts


def main() -> int:
    database_url = os.environ.get("MERGEWORK_DATABASE_URL", "sqlite:///data/mergework.sqlite3")
    with session_scope(database_url) as session:
        issues = reconcile_accepted_work_payouts(session)
    if not issues:
        print("Payout reconciliation ok: no missing or duplicate accepted-work payments.")
        return 0
    print(f"Payout reconciliation found {len(issues)} issue(s):")
    for issue in issues:
        print(
            "- "
            f"{issue['problem']} "
            f"submission={issue['submission_id']} "
            f"bounty={issue['bounty_id']} "
            f"submitter={issue['submitter_account']} "
            f"url={issue['submission_url']} "
            f"detail={issue['detail']}"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
