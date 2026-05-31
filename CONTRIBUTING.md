# Contributing to MergeWork

MergeWork rewards accepted open-source work with MRWK. Good contributions are
small, verifiable, and easy for maintainers to review.

## Claiming Work

1. Choose an issue labeled `mrwk:bounty`.
2. Confirm the issue also has a `Reserved on MergeWork` comment and an open
   public bounty row with available capacity.
3. Do not claim proposed-work issues, pending `create_bounty` proposals, closed
   rounds, exhausted rounds, or work already covered by another useful PR.
4. Comment that you are working on it if nobody has an active attempt.
5. Keep the pull request focused on the issue.
6. Include test evidence, screenshots, or reproduction steps when relevant.
7. Wait for maintainer review. Payment happens only after `mrwk:accepted`; a
   pending `pay_bounty` proposal is not paid work until a public proof exists.

## Quality Expectations

- Use clear names and simple code.
- Add or update tests for changed behavior.
- Update docs for public behavior changes.
- Run `python scripts/docs_smoke.py` when changing docs, templates, examples, or onboarding.
- Do not submit generated noise, duplicate reports, or unrelated rewrites.
- Do not claim payout, acceptance, or ledger status that has not happened.

## Security Work

Report private security findings through the security policy. Public issues and
ledger proofs must not contain exploit details before maintainers approve
publication.
