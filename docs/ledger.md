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

The read-only Phase 2A snapshot exporter is the first boring foundation for
that path. It exports deterministic JSON from committed ledger entries only:

```bash
python scripts/export_ledger_snapshot.py > ledger-snapshot.json
python scripts/export_ledger_snapshot.py --schema > ledger-snapshot.schema.json
```

Phase 2B adds deterministic Merkle artifacts for the same snapshot balances:

```bash
python scripts/export_ledger_snapshot_merkle.py > ledger-snapshot-root.json
python scripts/export_ledger_snapshot_merkle.py --account github:alice > ledger-snapshot-proof.json
```

The snapshot includes schema/version metadata, a generated UTC timestamp, source
metadata, the latest ledger sequence and entry hash, the fixed genesis supply in
integer microunits, deterministically sorted account balances in integer
microunits, credited/debited/net supply totals, hash-chain verification, and
fixed-supply conservation verification.

Merkle roots and account proofs are read-only verification artifacts. Account
leaves use a versioned canonical JSON object containing `account` and integer
`balance_microunits`. Internal nodes also hash versioned canonical JSON and
promote an unpaired final node at each level. The final root hash binds the
snapshot schema/version, latest ledger sequence/hash, tree size, and account
tree hash. Generated timestamp and source metadata do not affect the root.

Use a proof with the matching published root object when checking a balance.
Verification rejects changed account ids, balances, leaf indexes, sibling
hashes or order, tree size, root hash, or ledger anchor.

Snapshot `proposal_validation` is intentionally `partial`: the exporter verifies
committed ledger entries, the hash chain, and fixed-supply conservation, but it
does not replay every historical treasury proposal, challenge, or governance
rule. Pending treasury proposals are not committed ledger state.

This snapshot is read-only infrastructure. It is not a bridge, exchange,
off-ramp, custody path, relayer, redemption mechanism, price signal, or live
external-value claim.

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
