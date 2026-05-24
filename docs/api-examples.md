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

Read a single bounty with its internal `id` from `/api/v1/bounties`:

```bash
curl -s "$API_HOST/api/v1/bounties/<bounty_id>"
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

Read accepted-work activity summarized from proof-backed bounty payments:

```bash
curl -s "$API_HOST/api/v1/activity"
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

Call `get_ledger_entry` with the immutable ledger `sequence`, not a proof hash
or bounty id. The JSON-RPC response wraps a text content block; parse
`result.content[0].text` as JSON to read the same fields returned by
`/api/v1/ledger/<sequence>`.

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"get_ledger_entry","arguments":{"sequence":322}}}'
```

The parsed text payload includes fields such as `sequence`, `type`, `from`,
`to`, `amount_mrwk`, `reference`, `previous_hash`, `entry_hash`, nullable
`proof_hash`, and `created_at`.

Call `submit_work_proof` only for submission guidance. It does not claim or pay
a bounty by itself; accepted work still requires the GitHub-native bounty flow
and maintainer acceptance.

```bash
curl -s -X POST "$MCP_HOST/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"submit_work_proof","arguments":{}}}'
```
