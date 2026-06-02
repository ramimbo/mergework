from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.accounts import (
    ACCOUNT_TRANSACTION_TYPE_OPTIONS,
    ACCOUNT_TRANSACTION_TYPES,
    normalized_wallet_address,
)
from app.bounty_availability import normalize_bounty_availability_filter
from app.bounty_sorting import BOUNTY_SORT_LABELS, normalize_bounty_sort
from app.control_chars import contains_control_character
from app.db import session_scope
from app.ledger_views import account_ledger_transaction_types, account_ledger_transactions
from app.models import Wallet
from app.path_params import SQLITE_INTEGER_MAX, proof_hash_from_path
from app.query_validation import (
    reject_control_char_query_param,
    reject_noncanonical_int_query_param,
    reject_repeated_query_param,
)
from app.serializers import bounty_list_summary, wallet_to_dict
from app.status import (
    CURRENT_TRANSFER_PATHS,
    FUTURE_PATH_BOUNDARY,
    UNSUPPORTED_PUBLIC_PATHS_SUMMARY,
)


def _bounties_api_url(
    status: str | None,
    query_text: str,
    selected_sort: str,
    limit: int | None,
    repo: str,
    issue_number: int | None,
    selected_availability: str,
) -> str:
    params: list[tuple[str, str]] = []
    if status:
        params.append(("status", status))
    if query_text:
        params.append(("q", query_text))
    if repo:
        params.append(("repo", repo))
    if issue_number is not None:
        params.append(("issue_number", str(issue_number)))
    if selected_sort != "newest":
        params.append(("sort", selected_sort))
    if limit is not None:
        params.append(("limit", str(limit)))
    if selected_availability != "all":
        params.append(("availability", selected_availability))
    return f"/api/v1/bounties?{urlencode(params)}" if params else "/api/v1/bounties"


def _bounties_page_url(
    status: str | None,
    query_text: str,
    selected_sort: str,
    limit: int | None,
    repo: str,
    issue_number: int | None,
    selected_availability: str,
) -> str:
    params: list[tuple[str, str]] = []
    if status:
        params.append(("status", status))
    if query_text:
        params.append(("q", query_text))
    if repo:
        params.append(("repo", repo))
    if issue_number is not None:
        params.append(("issue_number", str(issue_number)))
    if selected_sort != "newest":
        params.append(("sort", selected_sort))
    if limit is not None:
        params.append(("limit", str(limit)))
    if selected_availability != "all":
        params.append(("availability", selected_availability))
    return f"/bounties?{urlencode(params, quote_via=quote)}" if params else "/bounties"


def public_bounties_context(
    bounties: list[dict[str, Any]],
    status: str | None,
    q: str | None,
    sort: str | None = None,
    limit: int | None = None,
    repo: str | None = None,
    issue_number: int | None = None,
    availability: str | None = None,
) -> dict[str, Any]:
    selected_status = status.strip().lower() if status is not None else None
    query_text = q.strip() if q is not None else ""
    selected_repo = repo.strip().lower() if repo is not None else ""
    selected_sort = normalize_bounty_sort(sort)
    selected_availability = normalize_bounty_availability_filter(availability)
    limit_options: tuple[int, ...] = (10, 25, 50, 100, 200)
    if limit is not None and limit not in limit_options:
        limit_options = tuple(sorted((*limit_options, limit)))
    return {
        "bounties": bounties,
        "summary": bounty_list_summary(bounties),
        "selected_status": selected_status,
        "query_text": query_text,
        "selected_repo": selected_repo,
        "selected_issue_number": issue_number,
        "selected_sort": selected_sort,
        "sort_options": BOUNTY_SORT_LABELS,
        "selected_limit": limit,
        "selected_availability": selected_availability,
        "limit_options": limit_options,
        "api_results_url": _bounties_api_url(
            selected_status,
            query_text,
            selected_sort,
            limit,
            selected_repo,
            issue_number,
            selected_availability,
        ),
        "clear_search_url": _bounties_page_url(
            selected_status,
            "",
            selected_sort,
            limit,
            selected_repo,
            issue_number,
            selected_availability,
        ),
        "status_filter_urls": {
            "all": _bounties_page_url(
                None,
                query_text,
                selected_sort,
                limit,
                selected_repo,
                issue_number,
                selected_availability,
            ),
            "open": _bounties_page_url(
                "open",
                query_text,
                selected_sort,
                limit,
                selected_repo,
                issue_number,
                selected_availability,
            ),
            "paid": _bounties_page_url(
                "paid",
                query_text,
                selected_sort,
                limit,
                selected_repo,
                issue_number,
                selected_availability,
            ),
            "closed": _bounties_page_url(
                "closed",
                query_text,
                selected_sort,
                limit,
                selected_repo,
                issue_number,
                selected_availability,
            ),
        },
    }


def wallets_page_context(session: Session, q: str | None = None) -> dict[str, Any]:
    if q is not None and contains_control_character(q):
        raise HTTPException(status_code=400, detail="q must not contain control characters")
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
    if transaction_type is not None and contains_control_character(transaction_type):
        raise HTTPException(
            status_code=400, detail="transaction type must not contain control characters"
        )
    selected_transaction_type = transaction_type.strip() if transaction_type is not None else ""
    if selected_transaction_type.lower() == "all":
        selected_transaction_type = ""
    available_transaction_types = account_ledger_transaction_types(session, wallet.address)
    selected_transaction_type = selected_transaction_type.lower()
    allowed_transaction_types = ACCOUNT_TRANSACTION_TYPES | set(available_transaction_types)
    if selected_transaction_type and selected_transaction_type not in allowed_transaction_types:
        static_options = [str(option["value"]) for option in ACCOUNT_TRANSACTION_TYPE_OPTIONS]
        custom_options = [
            entry_type
            for entry_type in available_transaction_types
            if entry_type not in ACCOUNT_TRANSACTION_TYPES and entry_type != "all"
        ]
        allowed = ", ".join([*static_options, *custom_options])
        raise HTTPException(
            status_code=400,
            detail=f"transaction type must be one of: {allowed}",
        )
    return {
        "wallet": wallet_to_dict(session, wallet),
        "transactions": account_ledger_transactions(
            session, wallet.address, entry_type=selected_transaction_type or None
        ),
        "transaction_types": available_transaction_types,
        "selected_transaction_type": selected_transaction_type,
    }


def ledger_entry_page_context(
    sequence: str, api_ledger_entry: Callable[[str], dict[str, Any]]
) -> dict[str, Any]:
    entry = api_ledger_entry(sequence)
    previous_sequence = entry["sequence"] - 1 if entry["sequence"] > 1 else None
    next_sequence = entry["sequence"] + 1
    try:
        api_ledger_entry(str(next_sequence))
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
        [str | None, str | None, str | None, int | None, str | None, int | None, str | None],
        list[dict[str, Any]],
    ],
    api_bounty: Callable[[str], dict[str, Any]],
    api_ledger: Callable[[], list[dict[str, Any]]],
    api_ledger_entry: Callable[[str], dict[str, Any]],
    api_proof: Callable[[str], dict[str, Any]],
) -> None:
    @app.get("/bounties", response_class=HTMLResponse)
    def bounties_page(
        request: Request,
        status: str | None = Query(None),
        q: str | None = Query(None),
        sort: str | None = Query(None),
        limit: int | None = Query(None, ge=1, le=200),
        repo: str | None = Query(None),
        issue_number: int | None = Query(None, ge=1, le=SQLITE_INTEGER_MAX),
        availability: str | None = Query(None),
    ) -> HTMLResponse:
        for name in ("status", "q", "limit", "sort", "repo", "issue_number", "availability"):
            reject_repeated_query_param(request, name)
        for name in ("status", "q", "sort", "repo", "availability"):
            reject_control_char_query_param(request, name)
        for name in ("limit", "issue_number"):
            reject_noncanonical_int_query_param(request, name)
        bounties = list_bounties_by_status(status, q, sort, limit, repo, issue_number, availability)
        return templates.TemplateResponse(
            request,
            "bounties.html",
            public_bounties_context(
                bounties,
                status,
                q,
                sort,
                limit,
                repo,
                issue_number,
                availability,
            ),
        )

    @app.get("/bounties/{bounty_id}", response_class=HTMLResponse)
    def bounty_page(request: Request, bounty_id: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "bounty_detail.html", {"bounty": api_bounty(bounty_id)}
        )

    @app.get("/ledger", response_class=HTMLResponse)
    def ledger_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "ledger.html", {"entries": api_ledger()})

    @app.get("/ledger/{sequence}", response_class=HTMLResponse)
    def ledger_entry_page(request: Request, sequence: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "ledger_entry.html", ledger_entry_page_context(sequence, api_ledger_entry)
        )

    @app.get("/wallets", response_class=HTMLResponse)
    def wallets_page(request: Request, q: str | None = Query(None)) -> HTMLResponse:
        reject_control_char_query_param(request, "q")
        reject_repeated_query_param(request, "q")
        with session_scope(db_url) as session:
            context = wallets_page_context(session, q)
        return templates.TemplateResponse(request, "wallets.html", context)

    @app.get("/wallets/{address}", response_class=HTMLResponse)
    def wallet_page(
        request: Request,
        address: str,
        type: str | None = Query(None),  # noqa: A002
    ) -> HTMLResponse:
        for value in request.query_params.getlist("type"):
            if contains_control_character(value):
                raise HTTPException(
                    status_code=400, detail="transaction type must not contain control characters"
                )
        reject_repeated_query_param(request, "type")
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
