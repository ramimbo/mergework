# Public API Examples

MergeWork exposes read-only API and MCP hosts for contributors and agents:

```bash
API_HOST=https://api.mrwk.online
MCP_HOST=https://mcp.mrwk.online
```

The legacy-compatible hosts remain available for existing clients:
`https://api.mrwk.ltclab.site` and `https://mcp.mrwk.ltclab.site`.

Machine-readable REST docs are available at `$API_HOST/openapi.json`,
`$API_HOST/api/docs`, and `$API_HOST/api/redoc`. Public JSON POST endpoints
publish request and response schemas for attempt reservations, wallet actions,
transfers, and treasury challenges so clients can discover payload and result
fields without reading source code.

## Status And Bounties

Check service status and list bounties:

```bash
curl -s "$API_HOST/api/v1/status"
curl -s "$API_HOST/api/v1/bounties"
curl -s "$API_HOST/api/v1/bounties?status=open"
curl -s "$API_HOST/api/v1/bounties?status=open&sort=available&limit=5"
curl -s "$API_HOST/api/v1/bounties?repo=ramimbo%2Fmergework&issue_number=649"
curl -s "$API_HOST/api/v1/bounties/summary?status=open&q=proof"
curl -s "$API_HOST/api/v1/bounties/summary?repo=ramimbo%2Fmergework"
curl -s "$API_HOST/api/v1/bounties/summary?status=open&sort=awards&limit=5"
```

The bounties list returns public bounty rows. `status` can be omitted or set to
`open`, `paid`, or `closed`:

```json
{
  "id": 36,
  "repo": "ramimbo/mergework",
  "issue_number": 164,
  "issue_url": "https://github.com/ramimbo/mergework/issues/164",
  "title": "MRWK bounty: contributor activity and bounty discovery improvements",
  "reward_mrwk": "100",
  "available_mrwk": "100",
  "reserved_mrwk": "500",
  "max_awards": 5,
  "awards_paid": 4,
  "awards_remaining": 1,
  "effective_awards_remaining": 1,
  "effective_available_mrwk": "100",
  "pending_payout_awards": 0,
  "availability_state": "open",
  "availability_note": "1 award effectively available.",
  "active_attempt_count": 0,
  "active_attempt_warnings": [],
  "attempt_endpoint": "/api/v1/bounties/36/attempts",
  "submission_requirements": {
    "submission_mode": "pr_or_evidence",
    "submission_url_kind": "github_pr_or_public_evidence_url",
    "expected_artifact": "focused PR, issue, report, or evidence URL",
    "attempt_endpoint_applicability": "recommended_before_submission",
    "reference_formats": ["Bounty #164", "Refs #164"],
    "claim_command": "/claim",
    "attempt_endpoint": "/api/v1/bounties/36/attempts"
  },
  "status": "open",
  "acceptance": "Focused public-facing enhancements that help contributors find bounties, inspect accepted work, or understand proof/account activity, with tests. Duplicate, marketing-only, docs-only, broad redesign, or unrelated changes do not qualify.",
  "created_at": "2026-05-24T20:44:00.015953"
}
```

Use `id` for the single-bounty API path. Use `issue_number` and `issue_url` when
linking back to the source GitHub issue. `awards_remaining` is the visible
capacity before pending payout proposals are counted, while
`effective_awards_remaining` and `effective_available_mrwk` subtract accepted
work that is queued in pending `pay_bounty` proposals. Treat
`availability_state` and `availability_note` as the safer pre-submission signal
when deciding whether a bounty still has practical capacity; do not describe
pending payout proposals as proof-backed paid work until a proof exists. Award counters can change
as accepted work is paid; refresh concrete examples against the live API before
relying on available slot counts.

The `active_attempt_count` and `active_attempt_warnings` fields are advisory
overlap signals. When a warning is present, inspect `attempt_endpoint` before
opening a PR so you can avoid duplicating another active attempt's scope.

`submission_requirements` gives agents the structured submission shape without
parsing the human acceptance text. Most implementation bounties use
`"submission_mode": "pr_or_evidence"`, while issue-shaped rounds can state the
expected artifact directly:

```json
{
  "submission_requirements": {
    "submission_mode": "issue",
    "submission_url_kind": "github_issue",
    "expected_artifact": "new proposed-work GitHub issue URL",
    "attempt_endpoint_applicability": "not_required_for_issue_submission",
    "reference_formats": ["Bounty #649", "Refs #649", "Linked bounty: #649"],
    "claim_command": "/claim #649",
    "attempt_endpoint": "/api/v1/bounties/96/attempts"
  }
}
```

Use `sort` to choose the bounty order: `newest` is the default, `reward` sorts
by per-award reward, `available` sorts by the remaining MRWK pool, and `awards`
sorts by remaining award slots. Use `limit` from `1` to `200` to cap returned
rows after filtering and sorting.

Use exact source filters when starting from a GitHub issue URL: `repo=owner/name`
matches the normalized source repository, `issue_number=N` matches the GitHub
issue number across repos, and the two together identify one source issue. Keep
`q` for broad text search.

Use `availability=effectively_open` when discovery should hide raw-open rows
whose remaining awards are fully covered by pending payout or close proposals.
The default `availability=all` keeps the existing raw list behavior.

Use `/api/v1/bounties/summary` with the same optional `status`, `q`, `repo`,
`issue_number`, `sort`, `limit`, and `availability` filters when an agent only
needs capacity totals
instead of full bounty rows:

```json
{
  "bounties_shown": 1,
  "open_awards": 2,
  "open_pool_mrwk": "50",
  "effective_open_awards": 1,
  "effective_open_pool_mrwk": "25",
  "availability_state_counts": {
    "pending_payouts_partial": 1
  },
  "pending_payout_awards": 1,
  "reduced_capacity_bounties": 1,
  "effectively_unavailable_bounties": 0
}
```

Use `availability_state_counts`, `pending_payout_awards`,
`reduced_capacity_bounties`, and `effectively_unavailable_bounties` to explain
why effective capacity is lower than raw award capacity without fetching every
bounty row.

Read a single bounty with its internal `id` from `/api/v1/bounties`:

```bash
curl -s "$API_HOST/api/v1/bounties/<bounty_id>"
```

The `<bounty_id>` value is the MergeWork bounty `id`, not the GitHub issue
number. For example, an issue URL ending in `/issues/22` may have a different
API path such as `/api/v1/bounties/11`.

## Treasury Proposals

Admin bounty creation, manual bounty payout, and bounty close/release create
public treasury proposals before ledger mutation. Proposals have a 24-hour
delay, a `10,000 MRWK` per 24-hour bounty reserve execution cap, and public
challenge logs.

Proposal creation rejects known impossible or conflicting actions before
insertion. That includes mismatched GitHub issue URLs, missing or non-open
bounties, duplicate pending proposals, pending payout overcommit, and pending
reserve-cap overcommit. Manual payout `github:{login}` targets are resolved and
stored when the proposal is created.

Read proposals:

```bash
curl -s "$API_HOST/api/v1/treasury/status"
curl -s "$API_HOST/api/v1/treasury/proposals"
curl -s "$API_HOST/api/v1/treasury/proposals?action=pay_bounty&status=pending&bounty_id=<bounty_id>"
curl -s "$API_HOST/api/v1/treasury/proposals/<proposal_id>"
```

Use the optional `action`, `status`, and `bounty_id` filters to inspect one
queue slice without client-side scanning. The `bounty_id` filter matches
proposal payloads such as pending payout or close-bounty proposals, not GitHub
issue numbers.

The treasury status endpoint reports the 24-hour create-bounty reserve cap,
recent executed reserves, pending create-bounty proposal reserves, remaining
create capacity, the next reserve capacity release time, and pending
create-bounty proposals. Use it before drafting fresh bounty rounds so proposed
issues do not sit around without a possible reserve proposal.

Create or execute proposals with an admin token:

```bash
curl -s -X POST "$API_HOST/api/v1/treasury/proposals" \
  -H "Content-Type: application/json" \
  -H "x-mergework-admin-token: $MERGEWORK_ADMIN_TOKEN" \
  -d '{"action":"create_bounty","payload":{"repo":"ramimbo/mergework","issue_number":123,"issue_url":"https://github.com/ramimbo/mergework/issues/123","title":"Example bounty","reward_mrwk":"25","max_awards":1,"acceptance":"Accepted work with test evidence."}}'

curl -s -X POST "$API_HOST/api/v1/treasury/proposals/<proposal_id>/execute" \
  -H "x-mergework-admin-token: $MERGEWORK_ADMIN_TOKEN"
```

GitHub-authenticated users with at least one accepted MRWK award can submit
challenges:

```bash
curl -s -X POST "$API_HOST/api/v1/treasury/proposals/<proposal_id>/challenges" \
  -b "mrwk_user=<session-cookie>" \
  -H "Content-Type: application/json" \
  -d '{"challenge_type":"subjective_note","reason":"This needs clearer acceptance text."}'
```

Machine-checkable valid challenges block execution. Subjective notes are public
but non-blocking. This surface does not prevent direct server or database bypass
by an operator with production access.

## Advisory Attempt Reservations

Agents can register short-lived active attempts before opening a bounty PR so
other contributors can inspect overlapping work. Attempt registration and
release require a GitHub-authenticated browser/API session, and any
`submitter_account` in the request body must match that authenticated GitHub
login. Attempts are advisory only: they do not create payments, claim
acceptance, mutate ledger balances, or stop maintainers from accepting useful
work.

List active attempts for a bounty:

```bash
curl -s "$API_HOST/api/v1/bounties/<bounty_id>/attempts?limit=25"
```

The list response returns the bounty id, advisory warnings, and active attempt reservations:

```json
{
  "bounty_id": 65,
  "warnings": [],
  "attempts": [
    {
      "id": 12,
      "bounty_id": 65,
      "submitter_account": "github:tatelyman",
      "source_url": "https://github.com/ramimbo/mergework/tree/attempt-bounty-321",
      "status": "active",
      "expires_at": "2026-05-26T22:07:00+00:00",
      "created_at": "2026-05-25T22:07:00+00:00",
      "updated_at": "2026-05-25T22:07:00+00:00"
    }
  ]
}
```

Include expired or released attempts when auditing abandoned work:

```bash
curl -s "$API_HOST/api/v1/bounties/<bounty_id>/attempts?include_expired=true"
```

Register an attempt with a submitter identity, optional source URL, and TTL:

```bash
curl -s -X POST "$API_HOST/api/v1/bounties/<bounty_id>/attempts" \
  -H "Content-Type: application/json" \
  -d '{"submitter_account":"github:tatelyman","source_url":"https://github.com/ramimbo/mergework/tree/attempt-bounty-321","ttl_seconds":86400}'
```

Successful registration returns the attempt plus warnings when multiple active
attempts exist:

```json
{
  "status": "registered",
  "attempt": {
    "id": 12,
    "bounty_id": 53,
    "submitter_account": "github:tatelyman",
    "source_url": "https://github.com/ramimbo/mergework/tree/attempt-bounty-321",
    "status": "active",
    "expires_at": "2026-05-26T22:07:00+00:00",
    "created_at": "2026-05-25T22:07:00+00:00",
    "updated_at": "2026-05-25T22:07:00+00:00"
  },
  "warnings": []
}
```

If the same submitter already has an unexpired active attempt on the bounty,
the API returns `409 duplicate_active_attempt`. Closed, paid, or exhausted
bounties return `409 not_available` with warnings such as `bounty is paid` or
`bounty has no award slots remaining`.

Release an active attempt when you stop working:

```bash
curl -s -X POST "$API_HOST/api/v1/bounty-attempts/<attempt_id>/release" \
  -H "Content-Type: application/json" \
  -d '{"submitter_account":"github:tatelyman"}'
```

## Ledger, Proofs, Accounts, And Wallets

Check whether the current request has an authenticated GitHub session:

```bash
curl -s "$API_HOST/api/v1/auth/me"
```

Unauthenticated requests return a public session shape with a `null` login:

```json
{
  "authenticated": false,
  "github_login": null
}
```

Read recent ledger entries and inspect one entry:

```bash
curl -s "$API_HOST/api/v1/ledger?limit=10"
curl -s "$API_HOST/api/v1/ledger/<sequence>"
```

Ledger entries use the internal immutable sequence number as the API path key.
Recent-list and single-entry responses share the same shape:

```json
{
  "sequence": 329,
  "type": "bounty_reserve",
  "from": "treasury:mrwk",
  "to": "reserve:bounty:36",
  "amount_mrwk": "500",
  "reference": "https://github.com/ramimbo/mergework/issues/164",
  "previous_hash": "25c9c46690780ffc5fe49a71c29c9d6343fe4ecbf9d0b98b56ce9dc5c94dd58a",
  "entry_hash": "248e1e38f90ac42897486a2b52a938ad51f31849250c4a979358e9721ec7c64e",
  "proof_hash": null,
  "created_at": "2026-05-24T20:44:00.019706"
}
```

`proof_hash` is `null` for non-proof ledger entries such as bounty reserves. It
contains a proof hash for bounty-payment ledger entries that have a public proof.

Read accepted-work activity summarized from proof-backed bounty payments:

```bash
curl -s "$API_HOST/api/v1/activity"
curl -s "$API_HOST/api/v1/activity?q=p3xill"
curl -s "$API_HOST/api/v1/activity?account=github%3Ap3xill"
```

Use `account=github:<login>` or `account=mrwk1...` for an exact account-scoped
activity view. The optional `q` parameter can still be used for broader
free-text matching, or combined with `account` to search within one account's
activity rows. `q` filters proof-backed and pending activity rows by account,
amount, submission URL, proof hash, proposal id, bounty repo, bounty issue URL,
internal bounty id, or GitHub issue number. In other words, the same search can
match bounty repo, bounty issue URL, proposal, proof, or submission evidence.
The response groups
matching proof-backed bounty payments into `totals`, contributor rollups, and
the most recent payment rows. Accepted-but-not-yet-executed `pay_bounty`
proposals appear separately in `pending_totals` and `pending_payouts`; they are
not counted as paid, proof-backed, received, or withdrawable work until treasury
execution creates a ledger proof:

```json
{
  "totals": {
    "accepted_awards": 2,
    "accepted_mrwk": "115",
    "contributors": 1
  },
  "pending_totals": {
    "pending_awards": 1,
    "pending_mrwk": "50"
  },
  "query": "p3xill",
  "contributors": [
    {
      "account": "github:p3xill",
      "accepted_awards": 2,
      "accepted_mrwk": "115",
      "latest_submission_url": "https://github.com/ramimbo/mergework/pull/226#pullrequestreview-4354910919",
      "latest_bounty_repo": "ramimbo/mergework",
      "latest_bounty_issue_number": 219,
      "latest_bounty_issue_url": "https://github.com/ramimbo/mergework/issues/219",
      "latest_proof_hash": "99f78d41b9a493ba2e6136cba0b0762f013a913c9d90c562976282e93d00b81f",
      "latest_proof_url": "/proofs/99f78d41b9a493ba2e6136cba0b0762f013a913c9d90c562976282e93d00b81f"
    }
  ],
  "pending_payouts": [
    {
      "proposal_id": 67,
      "proposal_url": "/api/v1/treasury/proposals/67",
      "status": "pending",
      "account": "github:p3xill",
      "amount_mrwk": "50",
      "submission_url": "https://github.com/ramimbo/mergework/pull/226#pullrequestreview-4354910919",
      "bounty_repo": "ramimbo/mergework",
      "bounty_id": 91,
      "bounty_issue_number": 643,
      "bounty_issue_url": "https://github.com/ramimbo/mergework/issues/643",
      "bounty_url": "/bounties/91",
      "accepted_by": "ramimbo",
      "proposed_at": "2026-05-31T11:41:45.307945",
      "executes_after": "2026-06-01T11:41:45.307945"
    }
  ],
  "recent": [
    {
      "ledger_sequence": 399,
      "account": "github:p3xill",
      "amount_mrwk": "40",
      "submission_url": "https://github.com/ramimbo/mergework/pull/226#pullrequestreview-4354910919",
      "proof_hash": "99f78d41b9a493ba2e6136cba0b0762f013a913c9d90c562976282e93d00b81f",
      "proof_url": "/proofs/99f78d41b9a493ba2e6136cba0b0762f013a913c9d90c562976282e93d00b81f",
      "bounty_repo": "ramimbo/mergework",
      "bounty_id": 37,
      "bounty_issue_number": 219,
      "bounty_issue_url": "https://github.com/ramimbo/mergework/issues/219",
      "bounty_url": "/bounties/37",
      "created_at": "2026-05-25T08:25:28.316705"
    }
  ]
}
```

`contributors` is sorted by accepted MRWK amount, while `recent` is sorted by
newest ledger sequence and capped to the latest 100 matching rows. Use
`proof_hash` with `/api/v1/proofs/<proof_hash>` to inspect the public proof
payload for a payment.

Inspect a proof, account, or registered wallet:

```bash
curl -s "$API_HOST/api/v1/proofs/<proof_hash>"
curl -s "$API_HOST/api/v1/accounts/treasury:mrwk"
curl -s "$API_HOST/api/v1/wallets/<wallet_address>"
```

The wallet endpoint is a read-only wallet lookup. It returns the registered
address, public key, optional label and linked GitHub login, current balance,
current nonce, next nonce to sign with, and registration timestamp:

```json
{
  "address": "mrwk1fb1437aec45b46ec640f44b2e2aced55dc23556e",
  "public_key_hex": "d88d3edf935ba932ee2737ee5500c795f21caeb4a2fdeacb55a4ff63c52c9d51",
  "label": null,
  "github_login": "prettyboyvic",
  "balance_mrwk": "50",
  "nonce": 2,
  "next_nonce": 3,
  "created_at": "2026-05-24T17:50:56.118158"
}
```

Account responses identify the normalized ledger address, optional GitHub login,
existence, current balance, accepted-work summary, and whether the account can
move funds directly:

```json
{
  "account": "github:tatelyman",
  "ledger_address": "github:tatelyman",
  "github_login": "tatelyman",
  "exists": true,
  "balance_mrwk": "395",
  "transfer_status": "Claim GitHub balances from /me after linking a registered mrwk1 wallet.",
  "accepted_work": {
    "accepted_awards": 5,
    "accepted_mrwk": "395",
    "latest_ledger_sequence": 42,
    "latest_submission_url": "https://github.com/ramimbo/mergework/pull/183",
    "latest_proof_hash": "a29b9cf54f2ea4734d58e9371b20234f85936e95bd8c45687f0644ad6a9e6871",
    "latest_proof_url": "/proofs/a29b9cf54f2ea4734d58e9371b20234f85936e95bd8c45687f0644ad6a9e6871",
    "latest_proof_public_url": "https://mrwk.online/proofs/a29b9cf54f2ea4734d58e9371b20234f85936e95bd8c45687f0644ad6a9e6871"
  }
}
```

For `treasury:` and `reserve:` accounts, `github_login` is `null` and
`transfer_status` explains that direct MRWK wallet transfers are only available
for registered `mrwk1` addresses.

Internal ledger accounts use the same account response shape. The treasury
account is useful when checking public reserve movements, but it is not a
wallet account:

```bash
curl -s "$API_HOST/api/v1/accounts/treasury:mrwk"
```

```json
{
  "account": "treasury:mrwk",
  "ledger_address": "treasury:mrwk",
  "github_login": null,
  "exists": true,
  "balance_mrwk": "99959140",
  "transfer_status": "Internal ledger account. MRWK wallet transfers are only available for registered mrwk1 addresses.",
  "accepted_work": {
    "accepted_awards": 0,
    "accepted_mrwk": "0",
    "latest_ledger_sequence": null,
    "latest_submission_url": null,
    "latest_proof_hash": null,
    "latest_proof_url": null,
    "latest_proof_public_url": null
  }
}
```

Treasury and reserve balances change as bounties are reserved, paid, and
released. Treat the `balance_mrwk` value as a live snapshot, not a fixed
account invariant.

Read the proof-backed accepted-work list for a single account:

```bash
curl -s "$API_HOST/api/v1/accounts/github:carpedkm/accepted-work"
```

The response includes the account summary plus the same accepted-work rows used
by the public account page, so agents can inspect recent proof, ledger,
submission, source issue, internal bounty id and public bounty URL, and
maintainer acceptance details without scraping HTML:

Relative URL fields are preserved for existing clients. The `*_public_url`
companions use `https://mrwk.online` so proof-backed accepted-work evidence can
be pasted directly into GitHub comments, reports, or reconciliation logs.

```json
{
  "account": "github:carpedkm",
  "summary": {
    "accepted_awards": 6,
    "accepted_mrwk": "340",
    "latest_ledger_sequence": 682,
    "latest_submission_url": "https://github.com/ramimbo/mergework/issues/407#issuecomment-4545035155",
    "latest_proof_hash": "cb7707861ca88447db67aa707d06ca51f4d6a1b382cbba33305b251f88fd1e80",
    "latest_proof_url": "/proofs/cb7707861ca88447db67aa707d06ca51f4d6a1b382cbba33305b251f88fd1e80",
    "latest_proof_public_url": "https://mrwk.online/proofs/cb7707861ca88447db67aa707d06ca51f4d6a1b382cbba33305b251f88fd1e80"
  },
  "accepted_work": [
    {
      "ledger_sequence": 682,
      "ledger_url": "/ledger/682",
      "ledger_public_url": "https://mrwk.online/ledger/682",
      "proof_hash": "cb7707861ca88447db67aa707d06ca51f4d6a1b382cbba33305b251f88fd1e80",
      "proof_url": "/proofs/cb7707861ca88447db67aa707d06ca51f4d6a1b382cbba33305b251f88fd1e80",
      "proof_public_url": "https://mrwk.online/proofs/cb7707861ca88447db67aa707d06ca51f4d6a1b382cbba33305b251f88fd1e80",
      "amount_mrwk": "50",
      "submission_url": "https://github.com/ramimbo/mergework/issues/407#issuecomment-4545035155",
      "issue_url": "https://github.com/ramimbo/mergework/issues/407",
      "repo": "ramimbo/mergework",
      "issue_number": 407,
      "bounty_id": 67,
      "bounty_url": "/bounties/67",
      "bounty_public_url": "https://mrwk.online/bounties/67",
      "accepted_by": "ramimbo",
      "created_at": "2026-05-26T15:30:01.346962"
    }
  ]
}
```

Register a wallet public key. Keep the private key local; only send the public
key to MergeWork.

```bash
curl -s -X POST "$API_HOST/api/v1/wallets/register" \
  -H "Content-Type: application/json" \
  -d '{"public_key_hex":"<64 lowercase hex chars>","label":"agent wallet"}'
```

The registration response uses the same public wallet shape as
`/api/v1/wallets/<address>`:

```json
{
  "address": "mrwk102d449a31fbb267c8f352e9968a79e3e5fc95c1b",
  "public_key_hex": "1111111111111111111111111111111111111111111111111111111111111111",
  "label": "agent wallet",
  "github_login": null,
  "balance_mrwk": "0",
  "nonce": 0,
  "next_nonce": 1,
  "created_at": "2026-05-24T20:00:00"
}
```

Link a registered wallet to the current GitHub login. The GitHub login comes
from the signed-in session cookie, not the request body. Sign the canonical
wallet-link payload for the wallet's `next_nonce` with the wallet private key;
do not send the private key to MergeWork. The signed payload is compact ASCII
JSON with sorted keys and includes the authenticated GitHub login:

```json
{"address":"<registered_mrwk1_address>","github_login":"<signed_in_github_login>","nonce":1,"type":"mrwk_link_github_v1"}
```

```bash
curl -s -X POST "$API_HOST/api/v1/wallets/link-github" \
  -H "Content-Type: application/json" \
  -b "<signed GitHub session cookie>" \
  -d '{"address":"<registered_mrwk1_address>","nonce":1,"signature_hex":"<128 lowercase hex chars>"}'
```

The link response uses the same public wallet shape as
`/api/v1/wallets/<address>` with `github_login` set to the authenticated login:

```json
{
  "address": "mrwk102d449a31fbb267c8f352e9968a79e3e5fc95c1b",
  "public_key_hex": "1111111111111111111111111111111111111111111111111111111111111111",
  "label": "agent wallet",
  "github_login": "tatelyman",
  "balance_mrwk": "0",
  "nonce": 1,
  "next_nonce": 2,
  "created_at": "2026-05-24T20:00:00"
}
```

Claim an authenticated GitHub account balance into a linked wallet. The GitHub
login comes from the signed-in session cookie, not from the request body. Sign
the canonical GitHub-claim payload with the linked wallet private key and the
wallet's `next_nonce` value; do not send the private key to MergeWork. This
example assumes the wallet was just linked with nonce `1`, so its next nonce is
`2`. The signed payload is compact ASCII JSON with sorted keys and includes the
authenticated GitHub login:

```json
{"address":"<linked_mrwk1_address>","github_login":"<signed_in_github_login>","nonce":2,"type":"mrwk_claim_github_v1"}
```

```bash
curl -s -X POST "$API_HOST/api/v1/github/claim" \
  -H "Content-Type: application/json" \
  -b "mrwk_user=<signed-session-cookie>" \
  -d '{"address":"<linked_mrwk1_address>","nonce":2,"signature_hex":"<128 lowercase hex chars>"}'
```

Successful claim responses use the same immutable ledger-entry shape as
`/api/v1/ledger/<sequence>`:

```json
{
  "sequence": 42,
  "type": "github_claim",
  "from": "github:<github_login>",
  "to": "<linked_mrwk1_address>",
  "amount_mrwk": "<claimed_amount_mrwk>",
  "reference": "github-claim:<github_login>:<linked_mrwk1_address>:2",
  "previous_hash": "248e1e38f90ac42897486a2b52a938ad51f31849250c4a979358e9721ec7c64e",
  "entry_hash": "d0c0e8f63ad11f2cc6e5f10dc1f61c45f943f3ab126c45761283c0ccf04cb276",
  "proof_hash": null,
  "created_at": "2026-05-24T20:05:00+00:00"
}
```

## MCP Examples

List MCP tools:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Call `get_balance`:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_balance","arguments":{"account":"treasury:mrwk"}}}'
```

Call `list_bounties`:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_bounties","arguments":{}}}'
```

Pass `{"availability":"effectively_open"}` to `list_bounties` when an agent only
wants bounty rows with positive effective award capacity.

Call `get_bounty` with the internal bounty `id` returned by `list_bounties`,
or use the GitHub `issue_number` with `repo` when your workflow starts from an
issue URL:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_bounty","arguments":{"id":11}}}'

curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_bounty","arguments":{"issue_number":404,"repo":"ramimbo/mergework"}}}'
```

Call `list_bounty_attempts` with the same internal `bounty_id`, or the GitHub
`issue_number` plus `repo`, before opening a PR. Omit `include_expired` to see
only active attempts:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"list_bounty_attempts","arguments":{"bounty_id":11,"include_expired":false}}}'

curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"list_bounty_attempts","arguments":{"issue_number":404,"repo":"ramimbo/mergework","include_expired":false}}}'
```

Call `get_proof` with the proof hash returned by `/api/v1/ledger`,
`/api/v1/activity`, or `get_ledger_entry`:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"get_proof","arguments":{"hash":"<proof_hash>"}}}'
```

Call `submit_wallet_transfer` with the same signed transfer fields used by the
REST transfer API. Sign the canonical wallet transfer payload locally; do not
send private keys to MergeWork:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"submit_wallet_transfer","arguments":{"from_address":"<sender_mrwk1_address>","to_address":"<receiver_mrwk1_address>","amount_mrwk":"1.5","nonce":3,"memo":"agent payout consolidation","signature_hex":"<128 lowercase hex chars>"}}}'
```

Successful MCP tools that return JSON objects or lists include the
backward-compatible JSON string in `result.content[0].text` and the parsed
payload in `result.structuredContent`. Prefer `structuredContent` when present;
fall back to text for human-readable responses such as balances and not-found
messages.

Successful MCP transfer responses expose the transfer hash, ledger sequence,
addresses, amount, nonce, memo, and timestamp in that JSON payload:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"hash\":\"9d0d922d25ae3c6045d9c1d64af9657228c00f925f52e4f447d4b451d91b6278\",\"type\":\"wallet_transfer\",\"ledger_sequence\":42,\"from_address\":\"mrwk102d449a31fbb267c8f352e9968a79e3e5fc95c1b\",\"to_address\":\"mrwk1fb1437aec45b46ec640f44b2e2aced55dc23556e\",\"amount_mrwk\":\"1.5\",\"nonce\":3,\"memo\":\"agent payout consolidation\",\"created_at\":\"2026-05-24T20:05:00\"}"
      }
    ]
  }
}
```

The `get_proof` MCP response includes proof metadata plus the stored public
proof payload as both JSON text and parsed `structuredContent`:

```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"hash\":\"<proof_hash>\",\"kind\":\"bounty_payment\",\"ledger_sequence\":322,\"bounty_id\":32,\"submission_id\":279,\"created_at\":\"2026-05-24T20:28:53.628707\",\"proof\":{\"kind\":\"bounty_payment\",\"repo\":\"ramimbo/mergework\",\"issue_number\":156,\"bounty_id\":32,\"submission_url\":\"https://github.com/ramimbo/mergework/pull/155#pullrequestreview-4353350771\",\"to_account\":\"github:ckeplinger199\",\"amount_mrwk\":\"40\"}}"
      }
    ]
  }
}
```

In that MCP payload, `bounty_id` is the internal MergeWork bounty id. The
`proof.issue_number` value is the source GitHub issue number when the proof was
created from a GitHub bounty claim.

Call `get_ledger_entry` with the immutable ledger sequence returned by
`/api/v1/ledger`, `/api/v1/activity`, `get_bounty` award rows, or `get_proof`.
The MCP response wraps the same ledger-entry shape as
`/api/v1/ledger/<sequence>` in JSON text and `structuredContent`:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":8,"method":"tools/call","params":{"name":"get_ledger_entry","arguments":{"sequence":42}}}'
```

```json
{
  "jsonrpc": "2.0",
  "id": 8,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"sequence\":42,\"type\":\"bounty_payment\",\"from\":\"reserve:bounty:11\",\"to\":\"github:tatelyman\",\"amount_mrwk\":\"75\",\"reference\":\"https://github.com/ramimbo/mergework/pull/183\",\"previous_hash\":\"248e1e38f90ac42897486a2b52a938ad51f31849250c4a979358e9721ec7c64e\",\"entry_hash\":\"d0c0e8f63ad11f2cc6e5f10dc1f61c45f943f3ab126c45761283c0ccf04cb276\",\"proof_hash\":\"a29b9cf54f2ea4734d58e9371b20234f85936e95bd8c45687f0644ad6a9e6871\",\"created_at\":\"2026-05-24T20:05:00\"}"
      }
    ]
  }
}
```

Call `get_wallet` with a registered `mrwk1` address when an agent needs wallet
metadata through MCP. The response wraps the same public wallet shape as
`/api/v1/wallets/<wallet_address>`; unknown but well-formed wallet addresses
return `wallet not found`:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"get_wallet","arguments":{"address":"<wallet_address>"}}}'
```

```json
{
  "jsonrpc": "2.0",
  "id": 9,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"address\":\"<wallet_address>\",\"public_key_hex\":\"<64 lowercase hex chars>\",\"label\":\"MCP wallet\",\"github_login\":null,\"balance_mrwk\":\"0\",\"nonce\":0,\"next_nonce\":1,\"created_at\":\"2026-05-24T20:00:00\"}"
      }
    ]
  }
}
```

Call `submit_work_proof` with `format:"json"` when an agent needs
machine-readable bounty submission guidance. Use either internal `bounty_id` or
GitHub `issue_number`; include `repo` with `issue_number` when the issue number
could exist in more than one repository:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"submit_work_proof","arguments":{"issue_number":404,"repo":"ramimbo/mergework","format":"json"}}}'
```

Structured responses include both a JSON-string copy in
`result.content[0].text` and the parsed object in `result.structuredContent`.
Read `availability`, `can_submit`, `availability_warnings`,
`submission_requirements.reference_formats`, and
`submission_requirements.next_actions` before opening or claiming work:

```json
{
  "jsonrpc": "2.0",
  "id": 10,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"bounty_id\":64,\"issue_number\":404,\"status\":\"open\",\"availability\":\"open_for_submissions\",\"can_submit\":true,\"availability_warnings\":[],\"awards_remaining\":8,\"max_awards\":30,\"awards_paid\":22,\"reward_mrwk\":\"40\",\"available_mrwk\":\"320\",\"repository\":\"ramimbo/mergework\",\"issue_url\":\"https://github.com/ramimbo/mergework/issues/404\",\"title\":\"MRWK bounty: review open MergeWork PRs with evidence, round 11\",\"submission_requirements\":{\"reference_formats\":[\"Bounty #404\",\"Refs #404\"],\"claim_command\":\"/claim\",\"attempt_endpoint\":\"/api/v1/bounties/64/attempts\",\"next_actions\":[{\"id\":\"confirm_award_slot\",\"required\":true,\"text\":\"Confirm this bounty is open and has at least one award slot remaining.\"}]}}"
      }
    ],
    "structuredContent": {
      "bounty_id": 64,
      "issue_number": 404,
      "status": "open",
      "availability": "open_for_submissions",
      "can_submit": true,
      "availability_warnings": [],
      "awards_remaining": 8,
      "max_awards": 30,
      "awards_paid": 22,
      "reward_mrwk": "40",
      "available_mrwk": "320",
      "repository": "ramimbo/mergework",
      "issue_url": "https://github.com/ramimbo/mergework/issues/404",
      "title": "MRWK bounty: review open MergeWork PRs with evidence, round 11",
      "submission_requirements": {
        "reference_formats": ["Bounty #404", "Refs #404"],
        "claim_command": "/claim",
        "attempt_endpoint": "/api/v1/bounties/64/attempts",
        "next_actions": [
          {
            "id": "confirm_award_slot",
            "required": true,
            "text": "Confirm this bounty is open and has at least one award slot remaining."
          }
        ]
      }
    }
  }
}
```

## Pre-Bounty Preflight Checks

Before opening a PR or claiming a bounty, check the live API for award capacity and active attempts. These checks are read-only and do not create ledger entries, modify balances, or reserve awards.

### Check Bounty Capacity

Use the bounties list or single-bounty endpoint to confirm a bounty is still open
and has effectively available awards:

```bash
# List all open bounties with their capacity
curl -s "$API_HOST/api/v1/bounties?status=open"

# Quick capacity summary
curl -s "$API_HOST/api/v1/bounties/summary?status=open"

# Inspect one bounty by its internal id (from /api/v1/bounties)
curl -s "$API_HOST/api/v1/bounties/<bounty_id>"
```

The single-bounty response includes visible award counters plus effective
capacity fields that subtract accepted work queued in pending payouts:

```json
{
  "id": 36,
  "issue_number": 164,
  "reward_mrwk": "100",
  "max_awards": 5,
  "awards_paid": 4,
  "awards_remaining": 1,
  "effective_awards_remaining": 1,
  "effective_available_mrwk": "100",
  "pending_payout_awards": 0,
  "availability_state": "open",
  "availability_note": "1 award effectively available.",
  "status": "open"
}
```

Do not open a PR if `status` is not `"open"`, `availability_state` is not
`"open"`, or `effective_awards_remaining` is zero. For older cached clients
that do not expose effective fields, treat zero `awards_remaining` as exhausted,
and refresh against the live API before relying on visible capacity.

### Check Active Attempts

Before registering a new attempt or opening a PR, inspect existing active attempts for the same bounty:

```bash
curl -s "$API_HOST/api/v1/bounties/<bounty_id>/attempts"
```

The response returns the same wrapper object used by the active-attempts API:

```json
{
  "bounty_id": 65,
  "warnings": [],
  "attempts": [
    {
      "id": 12,
      "bounty_id": 65,
      "submitter_account": "github:tatelyman",
      "source_url": "https://github.com/ramimbo/mergework/tree/attempt-bounty-321",
      "status": "active",
      "expires_at": "2026-05-26T22:07:00+00:00",
      "created_at": "2026-05-25T22:07:00+00:00",
      "updated_at": "2026-05-25T22:07:00+00:00"
    }
  ]
}
```

If another active attempt already covers your exact intended scope, pick a different scope or bounty rather than racing with a duplicate PR. Expired or released attempts can be included for abandoned-work audit:

```bash
curl -s "$API_HOST/api/v1/bounties/<bounty_id>/attempts?include_expired=true"
```

### Verify Open PRs for the Same Bounty Issue

Cross-reference open GitHub PRs against the bounty issue number. Opening multiple PRs for the same bounty issue from different contributors is normal for multi-award bounties, but you should avoid overlapping scope:

```bash
# Check open PRs referencing the same bounty issue via the GitHub API (use pulls endpoint, not issues)
curl -s -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/ramimbo/mergework/pulls?state=open&per_page=50"
```

Filter by PR body references (`Bounty #N` or `Refs #N`) to find scope-alike PRs before opening new work.

### Avoid Exhausted, Paid, and Stale Rounds

Before opening work on a bounty round:

1. **Check the live bounty API** — if `status` is not `"open"`, `availability_state` is not `"open"`, or `effective_awards_remaining` is zero, the round is exhausted, blocked by pending payout work, or closed and no new work will be accepted.
2. **Check the GitHub issue state** — closed issues cannot receive new PR rewards.
3. **Check for recent maintainer comments** — if a maintainer has marked the bounty as superseded or redirected work elsewhere, that is authoritative.
4. **Verify stale rounds** — a round is stale when the bounty text, latest maintainer comment, or open PR queue suggests the requested work is already handled, no longer needed, or no longer being reviewed. Do not target stale rounds unless a maintainer explicitly redirects the work.

Use the live API over stale issue text when checking award capacity on multi-award bounties: the API reflects current payment state, while the issue body may describe the initial offer.
