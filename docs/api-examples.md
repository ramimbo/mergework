# Public API Examples

MergeWork exposes read-only API and MCP hosts for contributors and agents:

```bash
API_HOST=https://api.mrwk.ltclab.site
MCP_HOST=https://mcp.mrwk.ltclab.site
```

## Status And Bounties

Check service status and list bounties:

```bash
curl -s "$API_HOST/api/v1/status"
curl -s "$API_HOST/api/v1/bounties"
curl -s "$API_HOST/api/v1/bounties?status=open"
```

The bounties list returns public bounty rows. `status` can be omitted or set to
`open`, `paid`, or `closed`.

Each bounty row includes these fields:

```json
{
  "id": 39,
  "repo": "ramimbo/mergework",
  "issue_number": 229,
  "issue_url": "https://github.com/ramimbo/mergework/issues/229",
  "title": "MRWK bounty: public API examples accuracy, round 2",
  "reward_mrwk": "75",
  "reserved_mrwk": "450",
  "max_awards": 6,
  "awards_paid": 2,
  "awards_remaining": 4,
  "status": "open",
  "acceptance": "Focused public documentation PRs that make API or MCP examples match actual MergeWork response shapes, with evidence and docs/tests. Duplicate, invented, stale, style-only, or unrelated changes do not qualify.",
  "created_at": "2026-05-25T08:15:18.624552"
}
```

Use `id` for the single-bounty API path. Use `issue_number` and `issue_url` when
linking back to the source GitHub issue. Award counters (`awards_paid`,
`awards_remaining`) change as accepted work is paid; refresh concrete examples
against the live API before relying on available slot counts.

Read a single bounty with its internal `id` from `/api/v1/bounties`:

```bash
curl -s "$API_HOST/api/v1/bounties/<bounty_id>"
```

The `<bounty_id>` value is the MergeWork bounty `id`, not the GitHub issue
number. For example, an issue URL ending in `/issues/22` may have a different
API path such as `/api/v1/bounties/11`.

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
The `sequence` value increments monotonically as new entries are appended.
Recent-list and single-entry responses share the same shape.

Bounty-reserve entries record the initial reserve when a bounty is created:

```json
{
  "sequence": 392,
  "type": "bounty_reserve",
  "from": "treasury:mrwk",
  "to": "reserve:bounty:39",
  "amount_mrwk": "450",
  "reference": "https://github.com/ramimbo/mergework/issues/229",
  "previous_hash": "db0c7dbf2c10fe173c4364bb382cf75723401ea5515cab25838385b511390720",
  "entry_hash": "d5cc60f7b8359a12a752471577e91560509019299abc15d4ad9e63b5fd6089bb",
  "proof_hash": null,
  "created_at": "2026-05-25T08:15:18.628621"
}
```

`proof_hash` is `null` for non-proof ledger entries such as bounty reserves. It
contains a proof hash for bounty-payment ledger entries that have a public proof.

Read accepted-work activity summarized from proof-backed bounty payments:

```bash
curl -s "$API_HOST/api/v1/activity"
```

The activity response contains `totals`, `query`, `contributors`, and `recent`
keys. `totals` summarizes overall activity. `contributors` lists each account
with their accepted award count and latest proof. `recent` lists individual
bounty-payment entries:

```json
{
  "totals": {
    "accepted_awards": 354,
    "accepted_mrwk": "18185",
    "contributors": 66
  },
  "query": "",
  "contributors": [
    {
      "account": "github:ckeplinger199",
      "accepted_awards": 92,
      "accepted_mrwk": "5115",
      "latest_submission_url": "https://github.com/ramimbo/mergework/pull/174",
      "latest_proof_hash": "21f286c7dd51b5b81a5a1d1fe066a3914f3e1c864a47458d6e0333bc180796f4",
      "latest_proof_url": "/proofs/21f286c7dd51b5b81a5a1d1fe066a3914f3e1c864a47458d6e0333bc180796f4"
    }
  ],
  "recent": [
    {
      "ledger_sequence": 408,
      "account": "github:szw9999",
      "amount_mrwk": "40",
      "submission_url": "https://github.com/ramimbo/mergework/pull/225#pullrequestreview-4355217854",
      "proof_hash": "28952563edf452119ae6d0d878555f784c7bf4235fd180237af30af16ca7d621",
      "proof_url": "/proofs/28952563edf452119ae6d0d878555f784c7bf4235fd180237af30af16ca7d621",
      "bounty_id": 37,
      "bounty_issue_number": 219,
      "created_at": "2026-05-25T08:26:49.212157"
    }
  ]
}
```

Totals, contributor rankings, and recent entries change as new awards are paid.
Refresh against the live endpoint for current counts.

Inspect a proof, account, or registered wallet:

```bash
curl -s "$API_HOST/api/v1/proofs/<proof_hash>"
curl -s "$API_HOST/api/v1/accounts/treasury:mrwk"
curl -s "$API_HOST/api/v1/wallets/<wallet_address>"
```

The wallet endpoint is a read-only wallet lookup. It returns the registered
address, public key, optional label and linked GitHub login, current balance,
current nonce, next nonce to sign with, and registration timestamp.
`balance_mrwk` and `nonce` reflect the current on-ledger state and change as
transfers are processed:

```json
{
  "address": "mrwk1fb1437aec45b46ec640f44b2e2aced55dc23556e",
  "public_key_hex": "d88d3edf935ba932ee2737ee5500c795f21caeb4a2fdeacb55a4ff63c52c9d51",
  "label": null,
  "github_login": "prettyboyvic",
  "balance_mrwk": "175",
  "nonce": 2,
  "next_nonce": 3,
  "created_at": "2026-05-24T17:50:56.118158"
}
```

Account responses identify the normalized ledger address, optional GitHub login,
existence, current balance, and a `transfer_status` hint:

```json
{
  "account": "github:tatelyman",
  "ledger_address": "github:tatelyman",
  "github_login": "tatelyman",
  "exists": true,
  "balance_mrwk": "1290",
  "transfer_status": "Claim GitHub balances from /me after linking a registered mrwk1 wallet."
}
```

For `treasury:` and `reserve:` accounts, `github_login` is `null` and
`transfer_status` explains that direct MRWK wallet transfers are only available
for registered `mrwk1` addresses.

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

## MCP Examples

List MCP tools:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

The current tool set includes `list_bounties`, `get_bounty`, `get_balance`,
`register_wallet`, `get_wallet`, `submit_wallet_transfer`, `get_ledger_entry`,
`get_proof`, and `submit_work_proof`.

Call `get_balance`:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_balance","arguments":{"account":"treasury:mrwk"}}}'
```

The response is a plain-text balance string:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "treasury:mrwk: 99980315 MRWK"
      }
    ]
  }
}
```

Call `list_bounties`:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_bounties","arguments":{}}}'
```

Call `get_bounty` with the internal bounty `id` returned by `list_bounties`,
not the GitHub issue number:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_bounty","arguments":{"id":11}}}'
```

Call `get_proof` with the proof hash returned by `/api/v1/ledger`,
`/api/v1/activity`, or `get_ledger_entry`:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"get_proof","arguments":{"hash":"<proof_hash>"}}}'
```

The MCP response uses JSON-RPC content blocks. The first content block is a JSON
string with proof metadata plus the stored public proof payload:

```json
{
  "jsonrpc": "2.0",
  "id": 5,
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

Call `get_wallet` with a registered `mrwk1` address:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"get_wallet","arguments":{"address":"mrwk1fb1437aec45b46ec640f44b2e2aced55dc23556e"}}}'
```

The response returns a JSON-string wallet object inside a content block:

```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"address\":\"mrwk1fb1437aec45b46ec640f44b2e2aced55dc23556e\",\"public_key_hex\":\"d88d3edf935ba932ee2737ee5500c795f21caeb4a2fdeacb55a4ff63c52c9d51\",\"label\":null,\"github_login\":\"prettyboyvic\",\"balance_mrwk\":\"175\",\"nonce\":2,\"next_nonce\":3,\"created_at\":\"2026-05-24T17:50:56.118158\"}"
      }
    ]
  }
}
```

Call `submit_work_proof` to get instructions for submitting bounty work:

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"submit_work_proof","arguments":{}}}'
```

The response contains a plain-text instruction string:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "Open a focused PR or issue, reference the MRWK bounty, include test evidence, and wait for a maintainer to apply mrwk:accepted."
      }
    ]
  }
}
```
