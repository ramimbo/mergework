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


## Agent-Friendly Bounty Post Template

Maintainers should use this short, copy-pasteable shape for public MRWK bounty
issues. Stable headings let humans, GitHub search, API clients, and MCP tools
parse the reward, available award slots, work scope, acceptance gate, and review
evidence without guessing.

```text
Title: MRWK bounty: <amount> MRWK - <short scope>

## MRWK Bounty

Reward: `<amount> MRWK per accepted award`
Max awards: `<number>`

## Work Needed

<Specific useful work. Link affected files, docs, API routes, UI screens, or
issues. Keep the scope narrow enough for one focused PR or public artifact.>

## Acceptance Criteria

- <Observable condition required before `mrwk:accepted` is applied.>
- <Required docs, tests, screenshots, review notes, or reproduction evidence.>
- <Required comparison against current behavior or related docs when relevant.>

## How To Submit

Open a PR, issue comment, or linked public artifact that includes
`Bounty #<issue number>` or `Refs #<issue number>`. Use a closing reference only
when the bounty should close after the submission is accepted.

## Evidence Or Tests

- <Commands to run and paste output for.>
- <Screenshots, API responses, ledger proof, review checklist, or reproduction
steps maintainers need to verify the work.>

## Out Of Scope

- <Changes that should not be made.>
- <Claims that should not be made publicly.>

## Duplicate-Work And Stale-Work Rules

Single-award bounties pay the first accepted submission only. Multi-award
bounties pay at most `Max awards` accepted submissions. Before submitting, check
open linked PRs, comments, and attempt reservations. Rebase or refresh stale
work before review if the target files, APIs, or bounty text changed.
```

Public MRWK bounty text must avoid investment claims, price claims, fabricated
payout claims, liquidity claims, exchange claims, or bridge promises. State only
the work reward, acceptance conditions, and public proof that already exists.

## Submission Evidence Templates

Use the smallest template that makes the claim reviewable. Delete fields that do
not apply, but keep the evidence specific enough that a maintainer can reproduce
the work without reading unrelated context.

PR or fix claim:

```text
Summary:
Linked bounty:
Changed files:
Evidence:
Tests:
Out of scope:
```

Review claim:

```text
Reviewed PR:
Head commit:
Files inspected:
Verdict:
Validation:
```

Smoke-check or bug-report claim:

```text
Checked URL or command:
Expected:
Observed:
Concise note:
```

Discussion or decision-support claim:

```text
Discussion URL:
Category:
Maintainer decision this supports:
Non-goals:
```

Do not describe work as accepted, merged, or paid until the public GitHub label,
maintainer comment, or MRWK proof exists.

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

PR bounty submissions should link the bounty issue with `Bounty #<issue>` or
`Refs #<issue>`. Use a closing reference only when the issue should close after
that PR.

For bounty PRs, include the claim-window packet in the PR body: exact bounty
reference, intended files or surfaces, expected PR size, test plan, evidence,
and out-of-scope notes. If the diff grows beyond the expected size, split it or
explain why the larger review remains focused.

Paid bounty links are tracked in
[docs/paid-bounties.md](paid-bounties.md) and the public
[GitHub discussion](https://github.com/ramimbo/mergework/discussions/16).
