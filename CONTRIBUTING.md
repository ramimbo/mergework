# Contributing to MergeWork

MergeWork rewards accepted open-source work with MRWK. Good contributions are
small, verifiable, and easy for maintainers to review.

## Claiming Work

1. Choose an issue labeled `mrwk:bounty`.
2. Comment that you are working on it if nobody has an active attempt.
3. Keep the pull request focused on the issue.
4. Include test evidence, screenshots, or reproduction steps when relevant.
5. Wait for maintainer review. Payment happens only after `mrwk:accepted`.

## Quality Expectations

- Use clear names and simple code.
- Add or update tests for changed behavior.
- Update docs for public behavior changes.
- Run `python scripts/docs_smoke.py` when changing docs, templates, examples, or onboarding.
- Do not submit generated noise, duplicate reports, or unrelated rewrites.
- Do not claim payout, acceptance, or ledger status that has not happened.

## Preflight Checks

Before opening a bounty PR, draft the PR body locally and run the advisory
submission gate:

```bash
python scripts/submission_quality_gate.py --text-file pr-body.md --repo ramimbo/mergework
```

The gate checks for a bounty reference, summary, validation evidence, open award
capacity, active attempts, similar open PRs, and recent maintainer activity when
that public context is available. A warning is not an automatic rejection, but
fix missing evidence, duplicate scope, or closed bounty references before
submitting.

## Security Work

Report private security findings through the security policy. Public issues and
ledger proofs must not contain exploit details before maintainers approve
publication.
