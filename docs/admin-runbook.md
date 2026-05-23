# Admin Runbook

## Post a Bounty

1. Create or choose a GitHub issue.
2. Decide the MRWK amount using the reference tiers.
3. Add acceptance text that explains what counts as useful accepted work.
4. Use `/admin` or `POST /api/v1/bounties` with an admin token.
5. Add `mrwk:bounty` to the GitHub issue.

## Accept Work

### PR Bounties

1. Review the submission and test evidence.
2. Confirm the work matches the bounty acceptance text.
3. Confirm the labeler is listed in `MERGEWORK_GITHUB_ACCEPTED_LABELERS`.
4. Confirm the PR body closes the bounty issue, such as `Closes #3`.
5. Apply `mrwk:accepted` to the PR.
6. Confirm the webhook records one ledger payment to the PR author.
7. Add `mrwk:paid` after the explorer/API shows the proof.

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

`to_account` must be a registered `mrwk1...` wallet or `github:{login}`.

Manual payout checklist:

1. Verify the public proof and wallet address.
2. Pay through `POST /api/v1/bounties/{id}/pay`.
3. Confirm the explorer/API shows the proof.
4. Add `mrwk:paid`.

Do not apply `mrwk:accepted` to maintainer-authored bounty issues for payment;
those issues require the manual payout path.

### Final Checks

1. Confirm the webhook or admin API records one ledger payment.
2. Confirm the proof pays the intended contributor account.

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
