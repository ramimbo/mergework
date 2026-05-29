from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.accounts import normalized_wallet_address
from app.bounty_sorting import BOUNTY_SORT_LABELS, normalize_bounty_sort
from app.db import session_scope
from app.ledger_views import account_ledger_transaction_types, account_ledger_transactions
from app.models import Wallet
from app.path_params import proof_hash_from_path
from app.serializers import bounty_list_summary, wallet_to_dict
from app.status import (
    CURRENT_TRANSFER_PATHS,
    FUTURE_PATH_BOUNDARY,
    UNSUPPORTED_PUBLIC_PATHS_SUMMARY,
)


def _bounties_api_url(
    status: str | None, query_text: str, selected_sort: str, limit: int | None
) -> str:
    params: list[tuple[str, str]] = []
    if status:
        params.append(("status", status))
    if query_text:
        params.append(("q", query_text))
    if selected_sort != "newest":
        params.append(("sort", selected_sort))
    if limit is not None:
        params.append(("limit", str(limit)))
    return f"/api/v1/bounties?{urlencode(params)}" if params else "/api/v1/bounties"


def public_bounties_context(
    bounties: list[dict[str, Any]],
    status: str | None,
    q: str | None,
    sort: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    selected_status = status.strip().lower() if status is not None else None
    query_text = q.strip() if q is not None else ""
    selected_sort = normalize_bounty_sort(sort)
    limit_options: tuple[int, ...] = (10, 25, 50, 100, 200)
    if limit is not None and limit not in limit_options:
        limit_options = tuple(sorted((*limit_options, limit)))
    return {
        "bounties": bounties,
        "summary": bounty_list_summary(bounties),
        "selected_status": selected_status,
        "query_text": query_text,
        "selected_sort": selected_sort,
        "sort_options": BOUNTY_SORT_LABELS,
        "selected_limit": limit,
        "limit_options": limit_options,
        "api_results_url": _bounties_api_url(selected_status, query_text, selected_sort, limit),
    }


def wallets_page_context(session: Session, q: str | None = None) -> dict[str, Any]:
    query_text = q.strip() if q is not None else ""
    query = select(Wallet)
    if query_text:
        escaped_query = (
            query_text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        like_query = f"%{escaped_query}%"
        query = query.where(
            or_(
                func.lower(Wallet.address).like(like_query, escape="\\"),
                func.lower(Wallet.label).like(like_query, escape="\\"),
                func.lower(Wallet.github_login).like(like_query, escape="\\"),
            )
        )
    wallets = session.scalars(query.order_by(Wallet.created_at.desc()).limit(100)).all()
    return {
        "wallets": [wallet_to_dict(session, wallet) for wallet in wallets],
        "query_text": query_text,
    }


def wallet_page_context(
    session: Session, address: str, transaction_type: str | None = None
) -> dict[str, Any]:
    normalized_address = normalized_wallet_address(address)
    wallet = session.get(Wallet, normalized_address)
    if wallet is None:
        raise HTTPException(status_code=404, detail="wallet not found")
    selected_transaction_type = transaction_type.strip() if transaction_type is not None else ""
    return {
        "wallet": wallet_to_dict(session, wallet),
        "transactions": account_ledger_transactions(
            session, wallet.address, entry_type=selected_transaction_type or None
        ),
        "transaction_types": account_ledger_transaction_types(session, wallet.address),
        "selected_transaction_type": selected_transaction_type,
    }


def ledger_entry_page_context(
    sequence: int, api_ledger_entry: Callable[[int], dict[str, Any]]
) -> dict[str, Any]:
    entry = api_ledger_entry(sequence)
    previous_sequence = entry["sequence"] - 1 if entry["sequence"] > 1 else None
    next_sequence = entry["sequence"] + 1
    try:
        api_ledger_entry(next_sequence)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        next_sequence = None
    return {
        "entry": entry,
        "previous_sequence": previous_sequence,
        "next_sequence": next_sequence,
    }


def register_public_routes(
    app: FastAPI,
    *,
    db_url: str,
    templates: Jinja2Templates,
    list_bounties_by_status: Callable[
        [str | None, str | None, str | None, int | None], list[dict[str, Any]]
    ],
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
        limit: int | None = Query(None, ge=1, le=200),
    ) -> HTMLResponse:
        bounties = list_bounties_by_status(status, q, sort, limit)
        return templates.TemplateResponse(
            request,
            "bounties.html",
            public_bounties_context(bounties, status, q, sort, limit),
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
            request, "ledger_entry.html", ledger_entry_page_context(sequence, api_ledger_entry)
        )

    @app.get("/wallets", response_class=HTMLResponse)
    def wallets_page(request: Request, q: str | None = Query(None)) -> HTMLResponse:
        with session_scope(db_url) as session:
            context = wallets_page_context(session, q)
        return templates.TemplateResponse(request, "wallets.html", context)

    @app.get("/wallets/{address}", response_class=HTMLResponse)
    def wallet_page(
        request: Request,
        address: str,
        type: str | None = Query(None),  # noqa: A002
    ) -> HTMLResponse:
        with session_scope(db_url) as session:
            context = wallet_page_context(session, address, type)
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
        return templates.TemplateResponse(
            request,
            "docs.html",
            {
                "current_transfer_paths": CURRENT_TRANSFER_PATHS,
                "future_path_boundary": FUTURE_PATH_BOUNDARY,
                "unsupported_public_paths_summary": UNSUPPORTED_PUBLIC_PATHS_SUMMARY,
            },
        )
