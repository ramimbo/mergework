from __future__ import annotations

import argparse
import json

from app.config import get_settings
from app.db import session_scope
from app.ledger.reconciliation import (
    reconcile_accepted_submission_payouts,
    reconciliation_findings_to_dicts,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report accepted submissions missing matching MRWK payment evidence."
    )
    parser.add_argument("--database-url", default=get_settings().database_url)
    parser.add_argument("--json", action="store_true", help="Print findings as JSON")
    args = parser.parse_args()

    with session_scope(args.database_url) as session:
        findings = reconcile_accepted_submission_payouts(session)

    if args.json:
        print(json.dumps(reconciliation_findings_to_dicts(findings), indent=2, sort_keys=True))
    elif not findings:
        print("payout reconciliation ok: no accepted submissions missing payment evidence")
    else:
        print(f"payout reconciliation found {len(findings)} issue(s):")
        for finding in findings:
            print(
                f"- {finding.code}: bounty #{finding.bounty_id}, "
                f"submission #{finding.submission_id}, {finding.submission_url} - {finding.detail}"
            )

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
