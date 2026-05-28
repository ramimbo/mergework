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

## Submission Evidence Templates

Use the smallest template that makes the claim reviewable. Delete fields that do
not apply, but keep the evidence specific enough that a maintainer can reproduce
the work without reading unrelated context.

## Agent-Readable Bounty Post Template

Maintainers should post MRWK bounties with stable headings so humans, GitHub
search, public API clients, and MCP agents can extract the same scope and reward
facts without guessing. Put the amount in the issue title and repeat it in the
body.

Issue title:

```text
MRWK bounty: <amount> MRWK - <short scope>
```

Issue body:

```text
## MRWK Bounty

Reward: `<amount> MRWK per accepted award`
Max awards: `<number>`

## Work Needed

Describe the useful accepted work in concrete, bounded terms.

## Acceptance Criteria

- Link the expected issue, PR, review, report, or proof surface.
- State the files, routes, APIs, docs, or behaviors that must be changed or checked.
- Explain what must be true before `mrwk:accepted` or an admin payout is recorded.

## How To Submit

Open a focused PR or public comment that links this issue with
`Bounty #<issue number>` or `Refs #<issue number>`.

## Evidence or Tests Required

- List the exact commands, URLs, screenshots, logs, or reproduction steps needed.
- Include expected and observed behavior for reviews, smoke checks, and bug reports.
- Keep private security details, secrets, wallet recovery data, and admin tokens out
  of public artifacts.

## Out of Scope

- List duplicate, broad rewrite, typo-only, style-only, speculative tokenomics,
  private-security-detail, price, liquidity, bridge, exchange, cash-out, or unrelated
  changes that do not qualify.

## Duplicate and Stale Work Rules

- Duplicate work is judged by the first useful, reviewable submission that matches
  the bounty criteria.
- Stale claims may be released or ignored when they do not include reviewable
  evidence or no longer match current repository behavior.
```

Agents need these fields because GitHub issue search, the public bounty API, and
MCP bounty tools expose title, issue URL, `reward_mrwk`, `max_awards`,
`awards_remaining`, labels, and public comments. Stable headings let agents match
the human bounty text to those machine-readable fields, avoid duplicate claims,
and produce focused evidence without inventing payout, price, exchange, liquidity,
bridge, or acceptance claims.

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

1. A maintainer proposes a bounty. After the 24-hour treasury delay, execution
   reserves MRWK from treasury. Multi-award bounties reserve
   `reward_mrwk * max_awards`.
2. A contributor submits an issue, PR, docs change, test, or report.
3. Automated checks may verify objective facts.
4. For PR submissions, a maintainer applies `mrwk:accepted` to the PR.
5. For comment or wallet-proof submissions, a maintainer proposes payment
   through the admin payout API and executes it after the treasury delay.
6. MergeWork creates one ledger payment and one public proof per accepted award.

Admin bounty creation, manual payouts, and bounty close/release use public
treasury proposals with delay, caps, and challenge logs. This protects normal
app paths. It does not prevent direct server or database bypass by an operator
with production access. PR webhook payouts still use the accepted-label flow.

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

Paid bounty records are proof-backed in the public activity feed, the activity
API, per-bounty accepted awards, ledger entries, and proof pages. The
[paid bounty guide](paid-bounties.md) points to those authoritative records.
Maintainers may post short human-readable payment summaries in the public
[GitHub discussion](https://github.com/ramimbo/mergework/discussions/16).
