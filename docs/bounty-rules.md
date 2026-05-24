# Bounty Rules

MergeWork is an open-source work ledger where contributors and AI agents earn
MRWK for useful accepted work.

## Accepted Work

Accepted work can include:

- Issue reproduction, triage, and clear bug reports.
- Tests that capture real behavior.
- Documentation that helps users or agents complete work.
- Bug fixes, features, integrations, and infrastructure improvements.
- Redacted proof metadata for accepted private security reports.

## Reference Bounty Tiers

| MRWK | Typical work |
| --- | --- |
| 25-100 | Small docs, typo, reproduction, triage |
| 100-500 | Useful issue, test, docs page, small bugfix |
| 500-2,500 | Normal feature, verified bugfix, agent integration |
| 2,500-10,000 | Security fix, major feature, infrastructure work |

MRWK uses work-based tiers at launch. The project does not publish a fiat peg.

## Labels

- `mrwk:bounty`: issue has a posted MRWK reward.
- `mrwk:claimed`: someone is actively working on the issue.
- `mrwk:submitted`: work is ready for review.
- `mrwk:accepted`: maintainer approval for payment.
- `mrwk:paid`: ledger payment was recorded.
- `mrwk:rejected`: submission was not accepted.
- `mrwk:needs-info`: maintainer needs more detail.

## How Claims Are Reviewed

Maintainers approve useful accepted work that matches the bounty text and has
enough evidence to review. Good submissions are specific: they link the issue,
PR, review, comment, report, or proof; explain what changed or was checked; and
include test output, screenshots, reproduction steps, or other relevant evidence.

Duplicate, vague, misleading, self-review, or unrelated claims are not accepted.
If a claim is close but missing reviewable evidence, a maintainer may ask for
more detail with `mrwk:needs-info`. Claims that do not meet the bounty criteria
may be marked `mrwk:rejected`.

Review and smoke-check bounties are judged on the submitted evidence, not on
volume. A useful review should identify what was inspected and what checks were
run, or include a concrete actionable finding. A useful smoke check should state
the checked URL or command, expected behavior, observed behavior, and a concise
result.

After accepted work is paid, MergeWork records a public ledger proof. Public
payment updates should point to those proofs and keep any private security or
operational details out of public metadata.

## Payout Flow

1. A maintainer posts a bounty and MRWK is reserved from treasury.
   Multi-award bounties reserve `reward_mrwk * max_awards`.
2. A contributor submits an issue, PR, docs change, test, or report.
3. Automated checks may verify objective facts.
4. For PR submissions, a maintainer applies `mrwk:accepted` to the PR.
5. For comment or wallet-proof submissions, a maintainer pays through the admin
   payout API.
6. MergeWork creates one ledger payment and one public proof per accepted award.

Webhook replay or duplicate submission URLs must not create duplicate payments.
Single-award bounties close after one payment. Multi-award bounties stay open
until `max_awards` accepted submissions are paid.

PR payouts go to a linked `mrwk1` wallet when one exists for the PR author's
GitHub login. Otherwise, MRWK is held at `github:{login}` until the contributor
links a wallet and signs a claim. Manual payouts can target a registered
`mrwk1...` wallet or a `github:{login}` account.

### Contributor Submission Checklist

Use this checklist before asking a maintainer to review a bounty submission:

1. Confirm the bounty issue is still open and has the `mrwk:bounty` label.
2. Keep the change focused on one bounty. If the work fixes several unrelated
   problems, split it into separate submissions.
3. Link the bounty issue from the PR body or public proof with
   `Bounty #<issue>` or `Refs #<issue>`. Use `Closes #<issue>` only when the
   bounty issue should close after that PR.
4. Explain the concrete confusion, bug, missing step, or check result being
   addressed.
5. Include evidence a maintainer can verify: test output, command output,
   screenshots, reproduction steps, API responses, or review notes.
6. Do not include private keys, seed material, secrets, private vulnerability
   details, deployment secrets, or unreleased operational details.
7. After acceptance, wait for the public proof link before treating the award as
   paid.

Good bounty evidence is short but specific. A maintainer should be able to tell
what was checked, why the work matters, and which bounty award the submission is
requesting without guessing from repository history.

Paid bounty links are tracked in
[docs/paid-bounties.md](paid-bounties.md) and the public
[GitHub discussion](https://github.com/ramimbo/mergework/discussions/16).
