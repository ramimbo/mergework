# Admin Runbook

## Post a Bounty

1. Create or choose a GitHub issue.
2. Decide the MRWK amount using the reference tiers.
3. Use the agent-readable bounty post template in
   [docs/bounty-rules.md](bounty-rules.md). Keep the issue title in the form
   `MRWK bounty: <amount> MRWK - <short scope>` and repeat the reward plus
   `max_awards` in the body.
4. Add acceptance text that explains what counts as useful accepted work, which
   files, routes, APIs, docs, or behaviors are in scope, and what evidence or
   tests a reviewer needs before `mrwk:accepted` or an admin payout is recorded.
5. Add explicit out-of-scope, duplicate-work, stale-work, and public artifact
   cautions. Do not include price, investment, exchange, liquidity, bridge,
   cash-out, fabricated payout, private security detail, secret, or token claims
   in public bounty text.
6. Set `max_awards` to the number of separate payouts allowed. Use `1` for
   a single-award bounty.
7. Use `/admin` or `POST /api/v1/bounties` with an admin token. This creates
   a public treasury proposal.
8. Execute the proposal after the 24-hour delay. Multi-award bounties reserve
   `reward_mrwk * max_awards` when the proposal executes.
9. Add `mrwk:bounty` to the GitHub issue.

## Treasury Proposals

Normal admin treasury actions are proposed before they execute:

- bounty creation
- manual bounty payout
- bounty close and reserve release

Public reads:

```bash
curl -s https://api.mrwk.ltclab.site/api/v1/treasury/proposals
curl -s https://api.mrwk.ltclab.site/api/v1/treasury/proposals/<proposal_id>
```

Execution requires an admin token and only works after the 24-hour delay:

```bash
curl -X POST https://api.mrwk.ltclab.site/api/v1/treasury/proposals/<proposal_id>/execute \
  -H "x-mergework-admin-token: $MERGEWORK_ADMIN_TOKEN"
```

Bounty reserve execution is capped at `10,000 MRWK` per 24-hour epoch. GitHub
users with at least one accepted MRWK award can submit proposal challenges.
Machine-checkable valid challenges block execution. Subjective challenges are
public notes and do not block by themselves.

Proposal creation rejects known impossible or conflicting actions before they
enter the public queue, including mismatched GitHub issue URLs, missing or
non-open bounties, duplicate pending proposals, and pending reserve-cap
overcommit.

This governance surface makes normal app-path treasury movement public,
delayed, capped, and challengeable. It does not prevent direct server or
database bypass by an operator with production access.

## Accept Work

### PR Bounties

1. Review the submission and test evidence.
2. Confirm the work matches the bounty acceptance text.
3. Confirm the labeler is listed in `MERGEWORK_GITHUB_ACCEPTED_LABELERS`.
4. Confirm the PR body links the bounty issue. Use `Bounty #3` or `Refs #3`
   for multi-award bounties; use `Closes #3` only when the bounty issue should
   close after that PR.
5. Apply `mrwk:accepted` to the PR.
6. Confirm the webhook records one ledger payment to the PR author.
7. Add `mrwk:paid` to the PR after the explorer/API shows the proof.
8. Add `mrwk:paid` to the bounty issue only after all awards are exhausted or
   the bounty is intentionally closed.

To close an open bounty without paying the remaining awards, propose a reserve
release:

```bash
curl -X POST https://api.mrwk.ltclab.site/api/v1/bounties/<id>/close \
  -H "content-type: application/json" \
  -H "x-mergework-admin-token: $MERGEWORK_ADMIN_TOKEN" \
  -d '{
    "reference": "https://github.com/ramimbo/mergework/issues/<issue>#close",
    "closed_by": "maintainer-login"
  }'
```

If the contributor linked a wallet, the payout goes directly to that `mrwk1`
address. If not, it goes to `github:{login}` and can be claimed later after
GitHub OAuth and wallet-linking.

### Comment or Wallet-Proof Bounties

Use the admin-token payout API when the accepted proof is a comment, wallet
address, or other non-PR submission:

```bash
curl -X POST https://api.mrwk.ltclab.site/api/v1/bounties/<id>/pay \
  -H "content-type: application/json" \
  -H "x-mergework-admin-token: $MERGEWORK_ADMIN_TOKEN" \
  -d '{
    "to_account": "mrwk1...",
    "submission_url": "https://github.com/ramimbo/mergework/issues/2#issuecomment-...",
    "accepted_by": "maintainer-login"
  }'
```

`to_account` must be a registered `mrwk1...` wallet or `github:{login}`. GitHub
targets are resolved once when the proposal is created; later wallet linking
does not change the stored payout destination. The API returns a pending
treasury proposal. After the delay, execute the proposal to record the ledger
payment and public proof. If the same bounty/submission URL is already paid,
the API returns `409` with `status: "already_paid"` and the existing proof links
instead of queuing another proposal.

Manual payout checklist:

1. Verify the public proof and wallet address.
2. Propose payment through `POST /api/v1/bounties/{id}/pay`.
3. Confirm the public proposal, wait for the delay, then execute it.
4. Confirm the explorer/API shows the proof.
5. Add `mrwk:paid` to the paid comment or submission when possible.
6. Add `mrwk:paid` to the bounty issue only after all awards are exhausted or
   the bounty is intentionally closed.

Do not apply `mrwk:accepted` to maintainer-authored bounty issues for payment;
those issues require the manual payout path.

### Webhook Outcome Inspection

Use the admin-token webhook events API to inspect recent delivery outcomes before
retrying labels or making manual payouts:

```bash
curl -s "https://api.mrwk.ltclab.site/api/v1/admin/webhook-events?status=missing_submitter" \
  -H "x-mergework-admin-token: $MERGEWORK_ADMIN_TOKEN"
```

The response includes delivery ID, GitHub event type, payload hash, processed
status, and timestamp. This is useful for confirming duplicate deliveries,
missing submitters, exhausted bounties, and already-paid submissions without
guessing from GitHub labels alone.
Use `limit` to control the number of delivery rows returned (`1` to `200`,
default `50`), for example:
`/api/v1/admin/webhook-events?status=missing_submitter&limit=100`.

### PR Queue Health

Use the queue-health script before accepting busy bounty rounds:

```bash
python scripts/pr_queue_health.py --repo ramimbo/mergework --format text
```

For a report that can be pasted directly into a GitHub issue, PR comment, or
payment-batch note, use Markdown output:

```bash
python scripts/pr_queue_health.py --repo ramimbo/mergework --format markdown
```

Live mode requires an authenticated GitHub CLI with access to the repository.
The command only reads PRs and issues; it does not close PRs, label issues, or
post comments. It reports missing bounty references, closed or exhausted bounty
references, dirty or unknown merge state, `mrwk:needs-info`, and likely duplicate
PR scope within the same bounty issue.

### Claim Inventory

Use the claim-inventory report when a busy bounty round needs a public,
read-only reconciliation pass across issue comments, PR comments, PR reviews,
and already-paid public proof data:

```bash
python scripts/claim_inventory.py --repo ramimbo/mergework --format markdown
```

The live command uses read-only `gh issue list/view` and `gh pr list/view`
calls plus public MergeWork API reads, including exact paid-award rows from
`/api/v1/activity` `recent[]`. It does not write GitHub comments, labels,
issue edits, admin-token API calls, local database queries, payout actions, or
private deployment reads. Use `--api-host` to point at another public API host
when reviewing staging-like public data:

```bash
python scripts/claim_inventory.py \
  --repo ramimbo/mergework \
  --api-host https://api.mrwk.ltclab.site \
  --format json
```

For offline payout reviews, save fixture data and run:

```bash
python scripts/claim_inventory.py --input claim-inventory.json --format markdown
```

Each output row includes `source_url`, `bounty_issue`, internal `bounty_id`
when known, `claimant`, `source_type`, `duplicate_key`, `likely_status`, and a
`proof_url` when the public activity/proof data already paid that source. The
report uses the parent PR URL when GitHub returns a review/comment object
without its own item URL, so use `source_type` and `claimant` to disambiguate
those rare rows. The
`likely_status` enum is:

- `already_paid`: public proof data maps the source URL to an existing proof.
- `unpaid_candidate`: the source looks like a live unpaid claim for a known bounty.
- `duplicate_candidate`: another source shares the same bounty/source duplicate key.
- `missing_bounty_ref`: the source looks claim-like but has no bounty reference.
- `unknown_bounty`: the source references a bounty absent from the public API/fixture.
- `ignored_or_unclear`: the source is public but not clearly actionable.

For offline checks, save fixture data and run:

```bash
python scripts/pr_queue_health.py --input queue.json --format json --fail-on-issues
```

`--fail-on-issues` exits nonzero when queue-health problems are found, which lets
maintainers add the check to local release or payout workflows without requiring
live GitHub access.

### Final Checks

1. Confirm the webhook or admin API records one ledger payment for that award.
2. Confirm the proof pays the intended contributor account.
3. Comment on the accepted work or bounty issue with the proof link, amount,
   recipient, and bounty reference.
4. Close or label the bounty according to its remaining award capacity.
5. Optionally post a short human-readable payment summary in
   [GitHub Discussions #16](https://github.com/ramimbo/mergework/discussions/16).
   Do not add manual payment rows to [docs/paid-bounties.md](paid-bounties.md);
   proof-backed activity, bounty, ledger, and proof endpoints are authoritative.

## GitHub OAuth

Production GitHub OAuth is configured for `https://mrwk.ltclab.site`.
Contributors use `/me` to sign in, link wallets, and claim older GitHub ledger
balances. If the GitHub app is rotated later, update deployment secrets outside
the repository and restart Docker Compose.

## Disputes

- Ask for concrete missing evidence with `mrwk:needs-info`.
- Use `mrwk:rejected` only when the submission is clearly not acceptable.
- Keep public comments short and specific.
- For security reports, keep private details out of public comments and proofs.

## Operations

- Database: `/srv/mergework/data/mergework.sqlite3`.
- Backups: `/srv/mergework/backups`.
- Health check: `GET /health`.
- Logs: `docker compose logs -f app caddy`.

## Pre-Bounty Readiness

Generate `MERGEWORK_GITHUB_WEBHOOK_SECRET`, `MERGEWORK_ADMIN_TOKEN`, and
`MERGEWORK_COOKIE_SECRET` with at least 32 random characters. Keep them outside
the repository.

Run the deploy gate before posting a bounty:

```bash
docker compose run --rm app python scripts/check_deploy_ready.py
```

Run a staging webhook payout dry run against a staging host:

```bash
MERGEWORK_STAGING_BASE_URL=https://staging.mrwk.example.test \
MERGEWORK_DRY_RUN_REPO=ramimbo/mergework \
docker compose run --rm app python scripts/staging_webhook_dry_run.py
```
