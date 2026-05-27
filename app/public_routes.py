from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts import normalized_wallet_address
from app.bounty_sorting import BOUNTY_SORT_LABELS, normalize_bounty_sort
from app.db import session_scope
from app.ledger_views import account_ledger_transactions
from app.models import Wallet
from app.path_params import proof_hash_from_path
from app.serializers import bounty_list_summary, wallet_to_dict


def public_bounties_context(
    bounties: list[dict[str, Any]],
    status: str | None,
    q: str | None,
    sort: str | None = None,
) -> dict[str, Any]:
    selected_status = status.strip().lower() if status is not None else None
    query_text = q.strip() if q is not None else ""
    selected_sort = normalize_bounty_sort(sort)
    return {
        "bounties": bounties,
        "summary": bounty_list_summary(bounties),
        "selected_status": selected_status,
        "query_text": query_text,
        "selected_sort": selected_sort,
        "sort_options": BOUNTY_SORT_LABELS,
    }


def _format_decimal_mrwk(value: Decimal) -> str:
    formatted = format(value.normalize(), "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted or "0"


def wallets_page_context(session: Session) -> dict[str, Any]:
    wallets = session.scalars(select(Wallet).order_by(Wallet.created_at.desc()).limit(100)).all()
    wallet_rows = [wallet_to_dict(session, wallet) for wallet in wallets]
    displayed_balance = sum((Decimal(row["balance_mrwk"]) for row in wallet_rows), Decimal("0"))
    return {
        "wallets": wallet_rows,
        "summary": {
            "wallets_shown": len(wallet_rows),
            "linked_github_wallets": sum(1 for row in wallet_rows if row.get("github_login")),
            "displayed_balance_mrwk": _format_decimal_mrwk(displayed_balance),
        },
    }


def wallet_page_context(session: Session, address: str) -> dict[str, Any]:
    normalized_address = normalized_wallet_address(address)
    wallet = session.get(Wallet, normalized_address)
    if wallet is None:
        raise HTTPException(status_code=404, detail="wallet not found")
    return {
        "wallet": wallet_to_dict(session, wallet),
        "transactions": account_ledger_transactions(session, wallet.address),
    }


def register_public_routes(
    app: FastAPI,
    *,
    db_url: str,
    templates: Jinja2Templates,
    list_bounties_by_status: Callable[[str | None, str | None, str | None], list[dict[str, Any]]],
    api_bounty: Callable[[int], dict[str, Any]],
    api_ledger: Callable[[], list[dict[str, Any]]],
    api_ledger_entry: Callable[[int], dict[str, Any]],
    api_proof: Callable[[str], dict[str, Any]],
) -> None:
    @app.get("/bounties", response_class=HTMLResponse)
    def bounties_page(
        request: Request,
        status: str | None = Query(None),
        q: str | None = Query(None),
        sort: str | None = Query(None),
    ) -> HTMLResponse:
        bounties = list_bounties_by_status(status, q, sort)
        return templates.TemplateResponse(
            request,
            "bounties.html",
            public_bounties_context(bounties, status, q, sort),
        )

    @app.get("/bounties/{bounty_id}", response_class=HTMLResponse)
    def bounty_page(request: Request, bounty_id: int) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "bounty_detail.html", {"bounty": api_bounty(bounty_id)}
        )

    @app.get("/ledger", response_class=HTMLResponse)
    def ledger_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "ledger.html", {"entries": api_ledger()})

    @app.get("/ledger/{sequence}", response_class=HTMLResponse)
    def ledger_entry_page(request: Request, sequence: int) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "ledger_entry.html", {"entry": api_ledger_entry(sequence)}
        )

    @app.get("/wallets", response_class=HTMLResponse)
    def wallets_page(request: Request) -> HTMLResponse:
        with session_scope(db_url) as session:
            context = wallets_page_context(session)
        return templates.TemplateResponse(request, "wallets.html", context)

    @app.get("/wallets/{address}", response_class=HTMLResponse)
    def wallet_page(request: Request, address: str) -> HTMLResponse:
        with session_scope(db_url) as session:
            context = wallet_page_context(session, address)
        return templates.TemplateResponse(request, "wallet_detail.html", context)

    @app.get("/transfer", response_class=HTMLResponse)
    def transfer_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "transfer.html")

    @app.get("/proofs/{proof_hash}", response_class=HTMLResponse)
    def proof_page(request: Request, proof_hash: str) -> HTMLResponse:
        normalized_proof_hash = proof_hash_from_path(proof_hash)
        return templates.TemplateResponse(
            request,
            "proof.html",
            {"proof": api_proof(normalized_proof_hash), "proof_hash": normalized_proof_hash},
        )

    @app.get("/docs", response_class=HTMLResponse)
    def docs_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "docs.html")
