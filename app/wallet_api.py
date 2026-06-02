from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from app.db import session_scope
from app.ledger.service import (
    LedgerError,
    link_wallet_to_github,
    register_wallet,
    submit_github_claim,
    submit_wallet_transfer,
)
from app.models import Wallet
from app.openapi_request_bodies import (
    SIGNED_WALLET_ACTION_BODY,
    WALLET_REGISTER_BODY,
    WALLET_TRANSFER_BODY,
)
from app.serializers import ledger_to_dict, wallet_to_dict, wallet_transfer_to_dict

JsonObjectLoader = Callable[[Request], Awaitable[dict[str, Any]]]
LoginDependency = Callable[[Request], str]
RequiredString = Callable[[dict[str, Any], str], str]
RequiredInteger = Callable[[dict[str, Any], str], int]
OptionalString = Callable[[dict[str, Any], str], str]
NormalizeWalletAddress = Callable[[str], str]
PostOnlyRoute = Callable[[], None]


def register_wallet_api_routes(
    app: FastAPI,
    *,
    db_url: str,
    require_github_login: LoginDependency,
    json_object: JsonObjectLoader,
    required_str: RequiredString,
    required_int: RequiredInteger,
    optional_str: OptionalString,
    normalized_wallet_address: NormalizeWalletAddress,
    post_only_route: PostOnlyRoute,
) -> None:
    @app.post("/api/v1/wallets/register", openapi_extra=WALLET_REGISTER_BODY)
    async def api_register_wallet(request: Request) -> dict[str, Any]:
        data = await json_object(request)
        with session_scope(db_url) as session:
            try:
                wallet = register_wallet(
                    session,
                    public_key_hex=required_str(data, "public_key_hex"),
                    label=optional_str(data, "label") if data.get("label") is not None else None,
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return wallet_to_dict(session, wallet)

    @app.get("/api/v1/wallets/register", include_in_schema=False)
    def api_register_wallet_get() -> None:
        post_only_route()

    @app.get("/api/v1/wallets/link-github", include_in_schema=False)
    def api_link_wallet_github_get() -> None:
        post_only_route()

    @app.get("/api/v1/wallets/{address}")
    def api_wallet(address: str) -> dict[str, Any]:
        address = normalized_wallet_address(address)
        with session_scope(db_url) as session:
            wallet = session.get(Wallet, address)
            if wallet is None:
                raise HTTPException(status_code=404, detail="wallet not found")
            return wallet_to_dict(session, wallet)

    @app.post("/api/v1/wallets/link-github", openapi_extra=SIGNED_WALLET_ACTION_BODY)
    async def api_link_wallet_github(
        request: Request, github_login: str = Depends(require_github_login)
    ) -> dict[str, Any]:
        data = await json_object(request)
        with session_scope(db_url) as session:
            try:
                wallet = link_wallet_to_github(
                    session,
                    address=required_str(data, "address"),
                    github_login=github_login,
                    nonce=required_int(data, "nonce"),
                    signature_hex=required_str(data, "signature_hex"),
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return wallet_to_dict(session, wallet)

    @app.post("/api/v1/github/claim", openapi_extra=SIGNED_WALLET_ACTION_BODY)
    async def api_github_claim(
        request: Request, github_login: str = Depends(require_github_login)
    ) -> dict[str, Any]:
        data = await json_object(request)
        with session_scope(db_url) as session:
            try:
                entry = submit_github_claim(
                    session,
                    address=required_str(data, "address"),
                    github_login=github_login,
                    nonce=required_int(data, "nonce"),
                    signature_hex=required_str(data, "signature_hex"),
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return ledger_to_dict(entry)

    @app.post("/api/v1/transfers", openapi_extra=WALLET_TRANSFER_BODY)
    async def api_submit_transfer(request: Request) -> dict[str, Any]:
        data = await json_object(request)
        with session_scope(db_url) as session:
            try:
                transfer = submit_wallet_transfer(
                    session,
                    from_address=required_str(data, "from_address"),
                    to_address=required_str(data, "to_address"),
                    amount_mrwk=required_str(data, "amount_mrwk"),
                    nonce=required_int(data, "nonce"),
                    memo=optional_str(data, "memo"),
                    signature_hex=required_str(data, "signature_hex"),
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return wallet_transfer_to_dict(transfer)
