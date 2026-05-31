# Admin Runbook

For the short bounty state machine shared by agents and maintainers, see
[docs/bounty-lifecycle.md](bounty-lifecycle.md). It defines when proposed,
pending, live, paid, and closed bounty work is claimable.

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
8. Execute the proposal after the 24-hour delay, or let the enabled production
   treasury executor execute it. Multi-award bounties reserve
   `reward_mrwk * max_awards` when the proposal executes.
9. If `MERGEWORK_GITHUB_ISSUE_TOKEN` is configured, execution adds
   `mrwk:bounty` and posts the `Reserved on MergeWork` claims-open comment. If
   the finalization result is skipped or failed, add the label/comment manually
   after confirming the public bounty row exists.

## Treasury Proposals

Normal admin treasury actions are proposed before they execute:

- bounty creation
- manual bounty payout
- bounty close and reserve release

Public reads:

```bash
curl -s https://api.mrwk.online/api/v1/treasury/status
curl -s https://api.mrwk.online/api/v1/treasury/proposals
curl -s https://api.mrwk.online/api/v1/treasury/proposals/<proposal_id>
```

Legacy-compatible admin API reads remain available at
`https://api.mrwk.ltclab.site` for existing scripts, but new examples should use
`https://api.mrwk.online`.

Execution requires an admin token and only works after the 24-hour delay:

```bash
curl -X POST https://api.mrwk.online/api/v1/treasury/proposals/<proposal_id>/execute \
  -H "x-mergework-admin-token: $MERGEWORK_ADMIN_TOKEN"
```

Do not execute production treasury proposals from a local `.env` unless the
token has just been validated against production. Prefer running execution from
the production host environment. Before any scripted execution, validate the
token with a harmless protected read:

```bash
curl -fsS "https://api.mrwk.online/api/v1/admin/webhook-events?limit=1" \
  -H "x-mergework-admin-token: $MERGEWORK_ADMIN_TOKEN"
```

If this check returns `401` or another unexpected auth error, stop. Do not keep
retrying proposal execution with that token.

Bounty reserve execution is capped at `10,000 MRWK` per 24-hour epoch. Check
`/api/v1/treasury/status` or the `/admin` treasury panel before opening fresh
rounds. It shows executed reserves in the rolling window, pending create-bounty
reserve, remaining create capacity, and the next capacity release time. GitHub
users with at least one accepted MRWK award can submit proposal challenges.
Machine-checkable valid challenges block execution. Subjective challenges are
public notes and do not block by themselves.

The admin treasury panel also shows projected capacity events. Use that table
to plan the next runway before current rounds empty: a pending create execution
does not free capacity immediately, because its reserve stays counted until it
leaves the rolling 24-hour execution window.

Proposal creation rejects known impossible or conflicting actions before they
enter the public queue, including mismatched GitHub issue URLs, missing or
non-open bounties, duplicate pending proposals, and pending reserve-cap
overcommit.

When `MERGEWORK_GITHUB_ISSUE_TOKEN` is set, successful `create_bounty`
execution also finalizes the GitHub issue. The token needs permission to add
labels and comments on `ramimbo/mergework` issues. Treasury execution still
succeeds if GitHub finalization is skipped or fails; check the proposal
`result.github_issue_finalization` field before posting any manual fallback.
A label-only partial update still needs the claims-open comment; a comment-only
partial update still needs the `mrwk:bounty` label. Confirm both the label and
the `Reserved on MergeWork` comment before treating the GitHub issue as live.

This governance surface makes normal app-path treasury movement public,
delayed, capped, and challengeable. It does not prevent direct server or
database bypass by an operator with production access.

### Production Treasury Executor

Production can run the `treasury-executor` Docker Compose service to execute
eligible treasury proposals without a maintainer being online at the exact
`executes_after` time. The service uses the production `.env`, the same
database volume as the app, and the same execution path as the manual admin
route.

Enable it only in the production `.env`:

```env
MERGEWORK_TREASURY_EXECUTOR_ENABLED=1
MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS=300
MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT=25
```

Deploy or restart the service with Docker Compose after editing production
`.env`. Do not run the executor from a local checkout or local `.env`.

```bash
docker compose up -d treasury-executor
docker compose logs -f treasury-executor
```

Each pass executes due pending proposals oldest-first up to the batch limit. If
one proposal fails, the executor logs that failure and continues with later due
proposals. It does not execute proposals before the 24-hour delay, and blocking
challenges still prevent execution through the normal treasury rules.

For due `create_bounty` proposals, successful execution should also finalize
the GitHub issue through the #630 path. Verify `result.github_issue_finalization`
on the proposal and confirm the issue has both `mrwk:bounty` and the
`Reserved on MergeWork` claims-open comment. If finalization is skipped, failed,
or partial, use the manual fallback rules above after confirming the public
bounty row exists.

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
curl -X POST https://api.mrwk.online/api/v1/bounties/<id>/close \
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
curl -X POST https://api.mrwk.online/api/v1/bounties/<id>/pay \
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
3. Confirm the public proposal, wait for the delay, then execute it manually or
   let the enabled production treasury executor execute it.
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
curl -s "https://api.mrwk.online/api/v1/admin/webhook-events?status=missing_submitter" \
  -H "x-mergework-admin-token: $MERGEWORK_ADMIN_TOKEN"
```

The response includes delivery ID, GitHub event type, payload hash, processed
status, and timestamp. This is useful for confirming duplicate deliveries,
missing submitters, exhausted bounties, and already-paid submissions without
guessing from GitHub labels alone.
Use `limit` to control the number of delivery rows returned (`1` to `200`,
default `50`), for example:
`/api/v1/admin/webhook-events?status=missing_submitter&limit=200`.

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
  --api-host https://api.mrwk.online \
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

Production GitHub OAuth must allow the exact callback
`https://mrwk.online/auth/github/callback`.
Contributors use `/me` to sign in, link wallets, and claim older GitHub ledger
balances. Keep the legacy callback
`https://mrwk.ltclab.site/auth/github/callback` available only if old browser
links still need it. If the GitHub app is rotated later, update deployment
secrets outside the repository and restart Docker Compose.

## Disputes

- Ask for concrete missing evidence with `mrwk:needs-info`.
- Use `mrwk:rejected` only when the submission is clearly not acceptable.
- Keep public comments short and specific.
- For security reports, keep private details out of public comments and proofs.

## Operations

- Database: `/srv/mergework/data/mergework.sqlite3`.
- Backups: `/srv/mergework/backups`.
- Health check: `GET /health`.
- Logs: `docker compose logs -f app caddy treasury-executor`.

## Pre-Bounty Readiness

Generate `MERGEWORK_GITHUB_WEBHOOK_SECRET`, `MERGEWORK_ADMIN_TOKEN`, and
`MERGEWORK_COOKIE_SECRET` with at least 32 random characters. Set
`MERGEWORK_GITHUB_ISSUE_TOKEN` if the app should finalize live bounty issues on
GitHub after proposal execution. Keep secrets outside the repository.

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
