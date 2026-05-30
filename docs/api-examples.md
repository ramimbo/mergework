# API Examples

This document provides example requests and responses for selected MergeWork API endpoints.

## Health

```bash
curl https://api.mrwk.ltclab.site/health
```

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

## Status

```bash
curl https://api.mrwk.ltclab.site/api/v1/status
```

```json
{
  "db_size": 123456,
  "total_bounties": 42,
  "total_ledger_entries": 100
}
```

## Bounty List

```bash
curl https://api.mrwk.ltclab.site/api/v1/bounties
```

```json
[
  {
    "id": 1,
    "title": "Example Bounty",
    "reward": 100,
    "status": "open"
  }
]
```

## Treasury Proposals

### List Proposals

```bash
curl https://api.mrwk.ltclab.site/api/v1/treasury/proposals
```

```json
[
  {
    "id": 1,
    "title": "Fund new feature",
    "amount": 500,
    "status": "pending",
    "created_at": "2025-03-01T12:00:00Z",
    "reserve_cap": 1000,
    "challenge_log": []
  }
]
```

### Get Proposal Detail

```bash
curl https://api.mrwk.ltclab.site/api/v1/treasury/proposals/1
```

```json
{
  "id": 1,
  "title": "Fund new feature",
  "amount": 500,
  "status": "pending",
  "created_at": "2025-03-01T12:00:00Z",
  "reserve_cap": 1000,
  "challenge_log": [],
  "executed_at": null
}
```

### Create Proposal (Admin)

```bash
curl -X POST https://api.mrwk.ltclab.site/api/v1/treasury/proposals \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "New feature funding", "amount": 500, "reserve_cap": 1000}'
```

```json
{
  "id": 2,
  "title": "New feature funding",
  "amount": 500,
  "status": "pending",
  "created_at": "2025-03-02T10:00:00Z",
  "reserve_cap": 1000,
  "challenge_log": []
}
```

### Execute Proposal (Admin)

```bash
curl -X POST https://api.mrwk.ltclab.site/api/v1/treasury/proposals/2/execute \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

```json
{
  "ledger_entry": {
    "sequence": 123,
    "type": "treasury_payout",
    "amount": 500,
    "timestamp": "2025-03-03T12:00:00Z"
  }
}
```

## Ledger Entries

```bash
curl https://api.mrwk.ltclab.site/api/v1/ledger?limit=10
```

```json
[
  {
    "sequence": 1,
    "type": "genesis",
    "amount": 21000000,
    "timestamp": "2025-01-01T00:00:00Z"
  }
]
```

## Wallet Info

```bash
curl https://api.mrwk.ltclab.site/api/v1/wallets/mrwk1abc123
```

```json
{
  "address": "mrwk1abc123",
  "balance": 1000,
  "nonce": 5
}
```

## Transfer

```bash
curl -X POST https://api.mrwk.ltclab.site/api/v1/transfers \
  -H "Content-Type: application/json" \
  -d '{"from": "mrwk1sender", "to": "mrwk1receiver", "amount": 50, "nonce": 3, "signature": "hex..."}'
```

```json
{
  "status": "accepted",
  "sequence": 200
}
```