# Paid MRWK Bounties

The MergeWork ledger, proof pages, and accepted-work activity feed are the
source of truth for MRWK bounty payments. This page is not manually updated for
every payout.

Use these public records instead:

- [Accepted work activity](https://mrwk.online/activity) for recent
  proof-backed bounty payments grouped by contributor.
- [Activity API](https://api.mrwk.online/api/v1/activity) for
  machine-readable accepted work, proof hashes, bounty issues, recipients, and
  amounts.
- `GET /api/v1/bounties/{id}` for the accepted awards attached to one bounty.
- [Ledger API](https://api.mrwk.online/api/v1/ledger) and
  `GET /api/v1/proofs/{proof_hash}` for individual ledger entries and public
  proof payloads.

Legacy-compatible endpoints remain available for existing links:

- [Legacy activity](https://mrwk.ltclab.site/activity)
- [Legacy Activity API](https://api.mrwk.ltclab.site/api/v1/activity)

Maintainers may still post short human-readable payment summaries in
[GitHub Discussions #16](https://github.com/ramimbo/mergework/discussions/16),
but those summaries mirror ledger proofs and are not the canonical payment
index.
