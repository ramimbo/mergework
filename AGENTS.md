# MergeWork Agent Instructions

## Project Purpose

MergeWork rewards accepted open-source work. Humans and AI agents earn MRWK only
when project maintainers accept useful issues, pull requests, docs, tests, or
verified reports.

## Public Artifact Hygiene

- Do not make investment claims, price claims, or fabricated payout claims.
- Do not publish private vulnerability details or unreleased exploit steps.
- Say MRWK starts as a native project coin and may support future snapshots,
  bridges, and onchain claims.
- Keep public prose short, direct, and human.

## Setup

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -e '.[dev]'
./.venv/bin/uvicorn app.main:app --reload
```

## Checks

Run these before opening a PR:

```bash
./.venv/bin/python -m pytest
./.venv/bin/python -m ruff format --check .
./.venv/bin/python -m ruff check .
./.venv/bin/python -m mypy app
```

## Architecture Map

- `app/ledger/`: fixed-supply MRWK ledger and proof logic.
- `app/wallets.py`: MRWK wallet address, canonical payload, and signature helpers.
- `app/webhooks/`: GitHub webhook verification and idempotent processing.
- `app/main.py`: FastAPI routes, pages, API, and MCP endpoint.
- `docs/`: contributor-facing and operator-facing documentation.
- `tests/`: behavior tests for ledger, webhooks, API, and MCP.

## Contribution Rules

- Keep PRs focused and small.
- Add or update tests for changed behavior.
- Update docs when public behavior changes.
- Ledger changes require supply conservation and hash-chain tests.
- Wallet changes require signature, nonce, replay, and spend tests.
- Webhook changes require signature verification and replay tests.

## Security Reports

Private findings stay private until maintainers approve disclosure. Public
ledger entries for security work must use redacted proof metadata only.
