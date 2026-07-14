# MergeWork Agent Instructions

## Project Purpose

MergeWork rewards accepted open-source work. Humans and AI agents earn MRWK only
when project maintainers accept useful issues, pull requests, docs, tests, or
verified reports.

## Bounty Lifecycle

Before treating work as payable, read
[docs/bounty-lifecycle.md](docs/bounty-lifecycle.md). Proposed-work and pending
create_bounty issues are not claimable. A bounty is claimable only after
`mrwk:bounty`, `Reserved on MergeWork`, and the public bounty row exist. Pending
pay_bounty proposals are not paid until a proof exists.

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

- `app/models.py`, `app/db.py`, `app/config.py`: SQLAlchemy models, database
  connection, and application configuration.
- `app/ledger/`: fixed-supply MRWK ledger, proof logic, and payout reconciliation.
- `app/wallets.py`: MRWK wallet address, canonical payload, and Ed25519 signature
  helpers.
- `app/webhooks/`: GitHub webhook signature verification and idempotent
  label-to-payout processing.
- `app/auth.py`, `app/me.py`: GitHub OAuth sign-in, session handling, and
  authenticated user profile.
- `app/bounty_api.py`, `app/bounty_attempts.py`, `app/activity.py`: bounty CRUD,
  advisory attempt reservations, and proof-backed accepted-work summaries.
- `app/admin.py`, `app/admin_routes.py`: admin-token gated endpoints for bounty
  posting, acceptance, payment, and webhook-event inspection.
- `app/mcp.py`, `app/mcp_tools.py`: Model Context Protocol server and tool
  implementations (`list_bounties`, `get_bounty`, `get_balance`, etc.).
- `app/main.py`: FastAPI application, route registration, and lifespan.
- `app/public_routes.py`, `app/hub.py`: public HTML pages and hub dashboard.
- `app/templates/`, `app/static/`: Jinja2 HTML templates, CSS, and client-side JS.
- `migrations/`, `alembic.ini`: database schema migrations.
- `scripts/`: quality gates, smoke checks, payout reconciliation, and deploy
  readiness checks.
- `tests/`: behavior tests for ledger, webhooks, API, MCP, and wallets.
- `docs/`: contributor-facing and operator-facing documentation.

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

PR bodies and submission drafts must not use premature payment/status wording such as
`Payout boundary`, legacy "not confirmed or withdrawable" phrasing, or reserved
paid/settled/received/withdrawable claim assertions. Use neutral `Submission status`
language instead. CI runs `scripts/check_pr_payment_language.py` on each pull request,
and `scripts/submission_quality_gate.py` enforces the same rule for draft checks.

