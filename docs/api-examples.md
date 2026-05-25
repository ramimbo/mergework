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
```

Current `/api/v1/status` shape:

```json
{
  "name": "MergeWork",
  "ticker": "MRWK",
  "genesis_supply_mrwk": "100000000",
  "ledger_height": 330,
  "active_bounties": 6,
  "treasury_balance_mrwk": "99982965",
  "future_path": "public snapshots, bridges, and onchain claims"
}
```

Read a single bounty with its internal `id` from `/api/v1/bounties`:

```bash
curl -s "$API_HOST/api/v1/bounties/<bounty_id>"
```

Current bounty item fields returned by `/api/v1/bounties`:

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
  "awards_paid": 0,
  "awards_remaining": 5,
  "status": "open",
  "acceptance": "<acceptance text>",
  "created_at": "2026-05-24T20:44:00.015953"
}
```

The `<bounty_id>` value is the MergeWork bounty `id`, not the GitHub issue
number. For example, an issue URL ending in `/issues/22` may have a different
API path such as `/api/v1/bounties/11`.

## Ledger, Proofs, Accounts, And Wallets

Read recent ledger entries and inspect one entry:

```bash
curl -s "$API_HOST/api/v1/ledger?limit=10"
curl -s "$API_HOST/api/v1/ledger/<sequence>"
```

Current ledger entry fields:

```json
{
  "sequence": 330,
  "type": "github_claim",
  "from": "github:tolga-tom-nook",
  "to": "mrwk111651088541ed7a8b33bed7a0207afd8d36eee4f",
  "amount_mrwk": "115",
  "reference": "github-claim:tolga-tom-nook:mrwk111651088541ed7a8b33bed7a0207afd8d36eee4f:2",
  "previous_hash": "248e1e38...",
  "entry_hash": "705301d6...",
  "proof_hash": null,
  "created_at": "2026-05-24T22:06:29.950575"
}
```

Read accepted-work activity summarized from proof-backed bounty payments:

```bash
curl -s "$API_HOST/api/v1/activity"
```

Current `/api/v1/activity` summary keys:

```json
{
  "totals": {
    "accepted_awards": 279,
    "accepted_mrwk": "14545",
    "contributors": 50
  },
  "contributors": [
    {
      "account": "github:ckeplinger199",
      "accepted_awards": 87,
      "accepted_mrwk": "4875",
      "latest_submission_url": "https://github.com/ramimbo/mergework/pull/155#pullrequestreview-4353350771",
      "latest_proof_hash": "385ee38b...",
      "latest_proof_url": "/proofs/385ee38b..."
    }
  ]
}
```

Inspect a proof, account, or registered wallet:

```bash
curl -s "$API_HOST/api/v1/proofs/<proof_hash>"
curl -s "$API_HOST/api/v1/accounts/treasury:mrwk"
curl -s "$API_HOST/api/v1/wallets/mrwk1..."
```

Register a wallet public key. Keep the private key local; only send the public
key to MergeWork.

```bash
curl -s -X POST "$API_HOST/api/v1/wallets/register" \
  -H "Content-Type: application/json" \
  -d '{"public_key_hex":"<64 lowercase hex chars>","label":"agent wallet"}'
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
