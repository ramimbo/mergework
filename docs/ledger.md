# MRWK Ledger

MRWK starts as a native project coin on the MergeWork ledger. The ledger keeps
public balances and proofs that may support future snapshots, bridges, or
onchain-claim experiments, but those paths are not live public off-ramps today.

## Units

- Genesis supply: `100,000,000 MRWK`.
- Decimal places: 6.
- Storage unit: integer microunits.
- Treasury account: `treasury:mrwk`.

## Bounty Reserve Model

Posting a bounty creates a reserve ledger entry from treasury to
`reserve:bounty:{id}`. Multi-award bounties reserve the per-award reward times
the maximum award count. Each accepted payout moves one award from that reserve
account to a linked `mrwk1` wallet, or to a temporary `github:{login}` account
when the contributor has not linked a wallet yet.

This keeps treasury balance useful: it shows MRWK not already reserved for open
bounties.

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

## Future Snapshot Path

Public ledger state can be snapshotted for bridge or onchain-claim experiments.
The public ledger and proof hashes are designed to make that process auditable.
Any public bridge, chain claim, exchange, or redemption path requires separate
maintainer and contributor discussion before implementation. The current public
app does not operate a BTC, USDC, fiat, bridge, exchange, or off-ramp.

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

GitHub payout accounts still exist for contributors who were paid before linking
a wallet. A linked wallet can sign a claim payload to move the full positive
`github:{login}` balance into the wallet.

Balances and proofs are inspectable in the explorer. The ledger remains the
source of truth for spendable MRWK balances.

## Current Transfer Paths

MRWK transfer support is currently limited to native MergeWork ledger paths:

1. Maintainer-approved work pays either a linked `mrwk1` wallet or a temporary
   native `github:{login}` account.
2. A contributor can link a registered `mrwk1` wallet with GitHub OAuth and a
   wallet signature, then claim the full positive `github:{login}` balance into
   that wallet.
3. Registered `mrwk1` wallets can send signed wallet-to-wallet transfers to
   other registered `mrwk1` wallets. The ledger verifies the Ed25519 signature,
   source address, amount, and next nonce before recording the transfer.

These are ledger-native transfers only. MergeWork does not currently provide a
public BTC, USDC, fiat, bridge, exchange, or off-ramp. Treat future public
snapshots, bridges, and onchain claims as separate design work until maintainers
publish and accept an implementation plan.
