# Ledger Rules

- Preserve fixed supply: credits minus debits must equal genesis supply.
- Any ledger behavior change needs tests for hash-chain integrity.
- Store MRWK amounts as integer microunits.
- Never edit ledger rows in place outside tests.
