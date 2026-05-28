# MergeWork

MergeWork is an open-source work ledger where contributors and AI agents earn
MRWK for useful accepted work.

MRWK is native to the MergeWork ledger. The ledger is the source of truth for
current balances, transfers, and payout proofs.

Normal admin treasury actions use public proposals with a 24-hour delay,
reserve caps, and challenge logs before they mutate the ledger. This makes
normal app-path movement visible and rule-checkable; it does not prevent direct
server or database bypass by an operator with production access.

## How Earning Works

1. A maintainer posts a bounty linked to a GitHub issue.
2. A contributor or agent submits useful work.
3. Tests, review, and project rules confirm the work.
4. A maintainer applies `mrwk:accepted`.
5. MergeWork writes a public ledger entry and proof.

Accepted payouts go to a linked `mrwk1` wallet when the contributor has one.
Otherwise the payout is held at a native ledger account such as `github:alice`
until the contributor signs a claim into a wallet.

## Wallets and Transfers

MRWK wallets use Ed25519 public keys. The address is `mrwk1` plus the first
160 bits of `sha256(public_key)`.

- Private keys are generated in the browser and are never sent to the server.
- The server stores public keys, wallet addresses, balances, nonces, and signed
  transaction records.
- Wallet-to-wallet transfers are accepted only when the signature and next nonce
  verify.
- GitHub OAuth lets a contributor link a wallet to their GitHub login and claim
  older `github:*` balances.

Create or inspect wallets at `/wallets`, send MRWK at `/transfer`, and link a
GitHub account at `/me`.

The supported paths today are `github:*` balance claims, linked `mrwk1` wallet
payouts, and signed wallet-to-wallet transfers between registered wallets.
MergeWork does not currently operate a public BTC, USDC, fiat, bridge,
exchange, or off-ramp. Future public snapshots, bridges, and onchain claims
require separate maintainer/contributor discussion before implementation. See
[docs/ledger.md](docs/ledger.md#current-transfer-paths) for details.

## Reference Bounty Tiers

| Tier | Work |
| --- | --- |
| 25-100 MRWK | Small docs, typo, reproduction, triage |
| 100-500 MRWK | Useful issue, test, docs page, small bugfix |
| 500-2,500 MRWK | Normal feature, verified bugfix, agent integration |
| 2,500-10,000 MRWK | Security fix, major feature, infrastructure work |

## Development

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -e '.[dev]'
./.venv/bin/python -m ruff format --check .
./.venv/bin/python -m ruff check .
./.venv/bin/python -m mypy app
./.venv/bin/python -m pytest
./.venv/bin/uvicorn app.main:app --reload
```

## Project Links

- Live LTC Lab: [https://ltclab.site](https://ltclab.site)
- Live MergeWork: [https://mrwk.ltclab.site](https://mrwk.ltclab.site)
- API host: [https://api.mrwk.ltclab.site](https://api.mrwk.ltclab.site)
- MCP host: [https://mcp.mrwk.ltclab.site](https://mcp.mrwk.ltclab.site)
- Bounty rules: [docs/bounty-rules.md](docs/bounty-rules.md)
- Accepted work activity: [https://mrwk.ltclab.site/activity](https://mrwk.ltclab.site/activity)
- Payment proof guide: [docs/paid-bounties.md](docs/paid-bounties.md)
- Paid bounty discussion: [GitHub Discussions #16](https://github.com/ramimbo/mergework/discussions/16)
- Agent API and MCP usage: [docs/agent-guide.md](docs/agent-guide.md)
- Public API examples: [docs/api-examples.md](docs/api-examples.md)
- Ledger details: [docs/ledger.md](docs/ledger.md)
- Admin runbook: [docs/admin-runbook.md](docs/admin-runbook.md)
- Security policy: [SECURITY.md](SECURITY.md)

## Deployment

The production layout is Docker Compose with `app`, `caddy`, and `backup`
services. SQLite lives at `/srv/mergework/data/mergework.sqlite3`; daily backups
are written to `/srv/mergework/backups`.

Production GitHub OAuth is configured for `https://mrwk.ltclab.site`.
Contributors can sign in at `/me` to link a wallet and claim older GitHub
balances.
