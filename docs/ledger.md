# MRWK Ledger

MRWK is native to the MergeWork ledger. The ledger is the source of truth for
current balances, transfers, and payout proofs.

## Units

- Genesis supply: `100,000,000 MRWK`.
- Decimal places: 6.
- Storage unit: integer microunits.
- Treasury account: `treasury:mrwk`.

## Bounty Reserve Model

Executing a bounty creation treasury proposal creates a reserve ledger entry
from treasury to `reserve:bounty:{id}`. Multi-award bounties reserve the
per-award reward times the maximum award count. Each accepted payout moves one
award from that reserve account to a linked `mrwk1` wallet, or to a temporary
`github:{login}` account when the contributor has not linked a wallet yet.

This keeps treasury balance useful: it shows MRWK not already reserved for open
bounties.

## Treasury Proposal Surface

Normal admin treasury actions use public proposals before ledger mutation:

- create bounty
- manual bounty payout
- close bounty and release reserve

Proposal execution is delayed 24 hours. Bounty reserve execution is capped at
`10,000 MRWK` per 24-hour epoch. GitHub users with accepted MRWK work can submit
machine-checkable challenges or public notes.

This makes normal app-path treasury movement visible and rule-checkable. It does
not prevent direct server or database bypass by an operator with production
access.

## Hash Chain

Each ledger entry hashes canonical JSON containing:

- sequence
- entry type
- from account
- to account
- amount
- reference
- previous hash
- timestamp

Entries must be sequential, each `previous_hash` must match the prior entry, and
the stored `entry_hash` must recompute from the entry payload.

## Current Transfer Paths

The supported MRWK transfer paths today are:

- `github:*` balance claims into a linked wallet.
- Payouts to linked `mrwk1` wallets.
- Signed wallet-to-wallet transfers between registered wallets.

A `github:*` account is a native ledger account for contributors who were paid
before linking a wallet. A linked wallet can sign a claim payload to move that
GitHub balance into the wallet.

MergeWork does not currently operate a public BTC, USDC, fiat, bridge,
exchange, or off-ramp. Future public snapshots, bridges, and onchain claims
require separate maintainer/contributor discussion before implementation.

## Future Snapshot Path

Public ledger state and proof hashes make future snapshot, bridge, or
onchain-claim experiments auditable if maintainers and contributors decide to
explore them.

## Wallets and Sending

MRWK supports native wallet addresses and signed transfers inside the ledger.
Wallet addresses look like `mrwk1...` and are derived from Ed25519 public keys:

- `address = "mrwk1" + sha256(raw_public_key_hex)[0:40]`
- `public_key_hex` is 32 raw Ed25519 public-key bytes encoded as lowercase hex.
- Signatures are Ed25519 signatures over canonical JSON.
- Wallet nonces start at `0`; each signed action must use `nonce + 1`.

Transfer payloads use this shape:

```json
{"type":"mrwk_transfer_v1","from_address":"mrwk1...","to_address":"mrwk1...","amount_microunits":1000000,"nonce":1,"memo":"optional"}
```

Balances and proofs are inspectable in the explorer. The ledger remains the
source of truth for spendable MRWK balances.
