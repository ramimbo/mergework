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
`open`, `paid`, or `closed`:

```json
{
  "id": 36,
  "repo": "ramimbo/mergework",
  "issue_number": 164,
  "issue_url": "https://github.com/ramimbo/mergework/issues/164",
  "title": "MRWK bounty: contributor activity and bounty discovery improvements",
  "reward_mrwk": "100",
  "reserved_mrwk": "500",
  "max_awards": 5,
  "awards_paid": 4,
  "awards_remaining": 1,
  "status": "open",
  "acceptance": "Focused public-facing enhancements that help contributors find bounties, inspect accepted work, or understand proof/account activity, with tests. Duplicate, marketing-only, docs-only, broad redesign, or unrelated changes do not qualify.",
  "created_at": "2026-05-24T20:44:00.015953"
}
```

Use `id` for the single-bounty API path. Use `issue_number` and `issue_url` when
linking back to the source GitHub issue. Award counters can change as accepted
work is paid; refresh concrete examples against the live API before relying on
available slot counts.

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
curl -s "$API_HOST/api/v1/activity?limit=10"
```

Use `limit` to choose how many recent accepted-work rows to return from 1 to
200.

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
existence, current balance, and whether the account can move funds directly:

```json
{
  "account": "github:tatelyman",
  "ledger_address": "github:tatelyman",
  "github_login": "tatelyman",
  "exists": true,
  "balance_mrwk": "395",
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
