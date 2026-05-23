# Webhook Rules

- Verify `X-Hub-Signature-256` before processing any payload.
- Use GitHub delivery ids for idempotency.
- Maintainer label `mrwk:accepted` authorizes PR payout; merge or CI alone does not.
- PR payouts must resolve a linked bounty issue and pay the PR author, not the
  bounty issue author.
- Maintainer-authored bounty issues require the admin payout path.
- Add replay tests for every new payout path.
