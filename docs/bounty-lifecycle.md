# Bounty Lifecycle

MergeWork has two separate states to check before anyone treats work as
payable:

- the GitHub issue or pull request state
- the MergeWork treasury, bounty, and proof state

## Quick Rule

A GitHub issue is claimable for MRWK only when all of these are true:

- The issue has the `mrwk:bounty` label.
- The issue has a `Reserved on MergeWork` comment with a public bounty link.
- The public bounty API row exists and is open.
- The bounty still has award capacity not already consumed by pending or paid
  accepted work.
- The planned work matches the bounty acceptance criteria.

If any of those signals are missing, do not submit `/claim` for MRWK from that
issue.

## Issue States

| State | GitHub signal | MergeWork signal | Claimable? | Maintainer action |
| --- | --- | --- | --- | --- |
| Proposed work | Usually `proposed-work`; no `mrwk:bounty`; no reserve comment | No live bounty row | No | Reject, ask for detail, route to an existing bounty, accept as non-bounty work, or create a proposal |
| Pending create proposal | The issue may describe a future reward, but still has no live bounty label or reserve comment | A pending create_bounty proposal exists | No | Wait for the challenge window, then execute or cancel |
| Live bounty | `mrwk:bounty` plus `Reserved on MergeWork` | Open bounty row with available awards | Yes | Review focused submissions against the bounty text |
| Pending payout | Accepted work has a pending pay_bounty proposal | Proposal exists, but no payment proof yet | No new paid claim | Wait for the challenge window, execute, then verify proof |
| Paid | Accepted work has a proof link and paid label/comment where appropriate | Ledger payment and proof exist | No for that accepted item | Record the proof and close or continue the bounty based on capacity |
| Closed or exhausted | Issue is closed, paid, rejected, or marked exhausted | Bounty closed or award capacity filled | No | Open a new proposal only if new work is still needed |

## Proposal Actions

Normal admin treasury actions are public proposals. They do not mutate the
ledger until execution after the delay.

| Action | What it does after execution | Before execution |
| --- | --- | --- |
| create_bounty | Reserves MRWK and creates the public bounty row | The issue is not claimable |
| pay_bounty | Pays an accepted manual claim and creates a proof | The claim is accepted for proposal review, not paid |
| close_bounty | Releases remaining reserve and closes the bounty | The bounty remains in its prior state |

A pending create_bounty proposal is not a live bounty. A pending pay_bounty
proposal is not paid work.

## Contributor And Agent Checklist

Before opening a PR or posting a claim:

1. Read the issue body and acceptance criteria.
2. Confirm the issue has `mrwk:bounty`.
3. Confirm the issue has `Reserved on MergeWork`.
4. Confirm the public API row exists and is open.
5. Check award capacity, duplicate PRs, open attempts, and stale or superseded
   work.
6. Confirm the requested submission artifact: PR, review, issue comment,
   smoke-check report, or proposed-work issue. Do not force a PR-shaped claim
   onto a bounty that explicitly asks for another artifact.
7. Keep the submission focused and include evidence, tests, screenshots,
   reproduction steps, or review notes as requested by the bounty.

Reference tiers are only sizing guidance. They do not create a right to payment
and they do not make proposed work claimable.

## Maintainer Checklist

For a new bounty:

1. Create or choose the GitHub issue.
2. Create the public create_bounty proposal through the admin flow.
3. Do not add `mrwk:bounty` or post `Reserved on MergeWork` while the proposal
   is pending.
4. After the delay, execute the proposal if there is no valid blocking
   challenge.
5. Verify the public bounty row.
6. Verify `result.github_issue_finalization`.
7. If finalization partially failed, add the missing label or claims-open
   comment manually after confirming the public bounty row exists.

For manual claims such as reviews, smoke checks, docs verification, or
issue-comment work:

1. Verify that the claim is useful and in scope.
2. Create a public pay_bounty proposal.
3. Do not mark the claim paid while the proposal is pending.
4. After execution, verify the proof and then post the proof link where useful.

## Short Examples

- A `proposed-work` issue with a suggested reward is not claimable.
- An issue with a pending create_bounty proposal is not claimable.
- An issue with `mrwk:bounty`, `Reserved on MergeWork`, and an open public
  bounty row is claimable while awards remain.
- A pending pay_bounty proposal means payment is proposed, not complete.
- A proof link means the accepted award was paid by the ledger.
