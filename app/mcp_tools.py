from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import func, or_, select

from app.accounts import normalized_account, normalized_wallet_address
from app.bounty_attempts import list_bounty_attempts
from app.bounty_availability import (
    filter_bounties_by_availability,
    normalize_bounty_availability_filter,
)
from app.bounty_sorting import normalize_bounty_sort, sort_bounties
from app.control_chars import contains_control_character
from app.db import session_scope
from app.ledger.service import format_mrwk, get_balance, register_wallet, submit_wallet_transfer
from app.ledger_views import ledger_entry_to_dict
from app.mcp_results import MCPTextResult
from app.mcp_work_proof import (
    generic_work_proof_guidance_json,
    work_proof_guidance,
    work_proof_guidance_json,
)
from app.models import Bounty, Proof, Wallet
from app.path_params import SQLITE_INTEGER_MAX, issue_number_search_value, proof_hash_from_path
from app.serializers import (
    bounties_to_dict,
    bounty_awards_to_dict,
    bounty_to_dict,
    public_utc_timestamp,
    wallet_to_dict,
    wallet_transfer_to_dict,
)

MCP_INTEGER_RE = re.compile(r"^(?:0|-?[1-9][0-9]*)$")
MCP_BOUNTY_SEARCH_QUERY_MAX_LENGTH = 500


def call_mcp_tool(
    database_url: str, name: str, args: dict[str, Any]
) -> str | dict[str, Any] | MCPTextResult:
    def int_arg(field: str) -> int:
        value = args[field]
        if isinstance(value, bool):
            raise ValueError(f"{field} must be an integer")
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str):
            if contains_control_character(value):
                raise ValueError(f"{field} must not contain control characters")
            if MCP_INTEGER_RE.fullmatch(value):
                try:
                    parsed = int(value)
                except ValueError as exc:
                    raise ValueError(f"{field} must be an integer") from exc
            else:
                raise ValueError(f"{field} must be an integer")
        else:
            raise ValueError(f"{field} must be an integer")
        if parsed < -SQLITE_INTEGER_MAX - 1 or parsed > SQLITE_INTEGER_MAX:
            raise ValueError(f"{field} is too large")
        return parsed

    def positive_int_arg(field: str) -> int:
        value = int_arg(field)
        if value <= 0:
            raise ValueError(f"{field} must be positive")
        return value

    def str_arg(field: str, *, allow_empty: bool = False) -> str:
        value = args[field]
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        if not allow_empty and value == "":
            raise ValueError(f"{field} must not be empty")
        return value

    def optional_str_arg(field: str, default: str = "") -> str:
        value = args.get(field, default)
        if value is None:
            return default
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        return value

    def optional_clean_str_arg(field: str) -> str | None:
        value = args.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        if contains_control_character(value):
            raise ValueError(f"{field} must not contain control characters")
        clean = value.strip()
        return clean or None

    def optional_bounty_search_query_arg() -> str | None:
        query = optional_clean_str_arg("q")
        if query is not None and len(query) > MCP_BOUNTY_SEARCH_QUERY_MAX_LENGTH:
            raise ValueError("q must be at most 500 characters")
        return query

    def output_format_arg() -> str:
        value = args.get("format", "text")
        if value is None:
            return "text"
        if not isinstance(value, str):
            raise ValueError("format must be a string")
        if contains_control_character(value):
            raise ValueError("format must not contain control characters")
        normalized = value.strip().lower()
        if normalized not in {"text", "json"}:
            raise ValueError("format must be text or json")
        return normalized

    def optional_repo_selector_arg() -> str | None:
        repo = optional_clean_str_arg("repo")
        if repo is None:
            return None
        if len(repo) > 200:
            raise ValueError("repo is too long")
        return repo.lower()

    def list_limit_arg(default: int = 25) -> int:
        if "limit" not in args or args.get("limit") is None:
            return default
        value = positive_int_arg("limit")
        if value > 100:
            raise ValueError("limit must be at most 100")
        return value

    def optional_bool_arg(field: str, default: bool = False) -> bool:
        value = args.get(field, default)
        if value is None:
            return default
        if not isinstance(value, bool):
            raise ValueError(f"{field} must be a boolean")
        return value

    def require_known_fields(*allowed_fields: str) -> None:
        unknown_fields = set(args) - set(allowed_fields)
        if unknown_fields:
            raise ValueError(f"unknown argument: {sorted(unknown_fields)[0]}")

    def bounty_by_issue_number(repo_selector: str | None) -> Bounty | None:
        issue_query = select(Bounty).where(Bounty.issue_number == positive_int_arg("issue_number"))
        if repo_selector is not None:
            issue_query = issue_query.where(func.lower(Bounty.repo) == repo_selector)
        bounties = session.scalars(issue_query.order_by(Bounty.id.desc()).limit(2)).all()
        if not bounties:
            return None
        if len(bounties) > 1:
            raise ValueError("issue_number matches multiple bounties")
        return bounties[0]

    def selected_bounty(
        internal_id_field: str,
        *,
        internal_id_aliases: tuple[str, ...] = (),
    ) -> Bounty | None:
        internal_id_fields = (internal_id_field, *internal_id_aliases)
        provided_internal_id_fields = [
            field for field in internal_id_fields if field in args and args.get(field) is not None
        ]
        has_internal_id = bool(provided_internal_id_fields)
        has_issue_number = "issue_number" in args and args.get("issue_number") is not None
        repo_selector = optional_repo_selector_arg()
        if len(provided_internal_id_fields) > 1:
            raise ValueError(
                "use "
                + " or ".join(provided_internal_id_fields)
                + ", not multiple internal id fields"
            )
        if has_internal_id and has_issue_number:
            raise ValueError(f"use {provided_internal_id_fields[0]} or issue_number, not both")
        if repo_selector is not None and not has_issue_number:
            raise ValueError("repo can only be used with issue_number")
        if has_internal_id:
            return session.get(Bounty, positive_int_arg(provided_internal_id_fields[0]))
        if has_issue_number:
            return bounty_by_issue_number(repo_selector)
        raise ValueError(f"{internal_id_field} or issue_number is required")

    def reject_unexpected_args(tool_name: str, allowed: set[str]) -> None:
        unexpected = sorted(set(args) - allowed)
        if unexpected:
            names = ", ".join(unexpected)
            raise ValueError(f"{tool_name} received unexpected argument(s): {names}")

    with session_scope(database_url) as session:
        if name == "list_bounties":
            require_known_fields(
                "status",
                "q",
                "repo",
                "issue_number",
                "sort",
                "limit",
                "availability",
            )
            status = optional_clean_str_arg("status") or "open"
            normalized_status = status.lower()
            if normalized_status not in {"open", "paid", "closed"}:
                raise ValueError("status must be one of: open, paid, closed")
            query = select(Bounty).where(Bounty.status == normalized_status)
            query_text = optional_bounty_search_query_arg()
            if query_text:
                escaped_query = (
                    query_text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                like_query = f"%{escaped_query}%"
                issue_number = issue_number_search_value(query_text)
                text_filter = or_(
                    func.lower(Bounty.repo).like(like_query, escape="\\"),
                    func.lower(Bounty.title).like(like_query, escape="\\"),
                    func.lower(Bounty.acceptance).like(like_query, escape="\\"),
                )
                if issue_number is not None:
                    text_filter = or_(text_filter, Bounty.issue_number == issue_number)
                query = query.where(text_filter)
            repo_selector = optional_repo_selector_arg()
            if repo_selector is not None:
                query = query.where(func.lower(Bounty.repo) == repo_selector)
            if "issue_number" in args and args.get("issue_number") is not None:
                query = query.where(Bounty.issue_number == positive_int_arg("issue_number"))
            sort = normalize_bounty_sort(optional_clean_str_arg("sort"))
            availability = normalize_bounty_availability_filter(
                optional_clean_str_arg("availability")
            )
            limit = list_limit_arg()
            if sort == "newest" and availability == "all":
                newest_bounties = session.scalars(
                    query.order_by(Bounty.id.desc()).limit(limit)
                ).all()
                return json.dumps(bounties_to_dict(newest_bounties, session=session))
            bounties = session.scalars(query.order_by(Bounty.id.desc())).all()
            sorted_bounties = sort_bounties(
                filter_bounties_by_availability(
                    bounties_to_dict(bounties, session=session),
                    availability,
                ),
                sort,
            )
            return json.dumps(sorted_bounties[:limit])
        if name == "get_bounty":
            bounty = selected_bounty("id", internal_id_aliases=("bounty_id",))
            if bounty is None:
                return "bounty not found"
            bounty_data = bounty_to_dict(bounty, session=session)
            if optional_bool_arg("include_awards"):
                bounty_data["awards"] = bounty_awards_to_dict(session, bounty.id)
            return json.dumps(bounty_data)
        if name == "list_bounty_attempts":
            bounty = selected_bounty("bounty_id", internal_id_aliases=("id",))
            if bounty is None:
                return "bounty not found"
            attempt_listing = list_bounty_attempts(
                session,
                bounty,
                include_expired=optional_bool_arg("include_expired"),
                limit=list_limit_arg(),
            )
            return {
                "bounty_id": bounty.id,
                "issue_number": bounty.issue_number,
                "status": bounty.status,
                "warnings": attempt_listing["warnings"],
                "attempts": attempt_listing["attempts"],
            }
        if name == "get_balance":
            account = normalized_account(str_arg("account"))
            balance_microunits = get_balance(session, account)
            balance_mrwk = format_mrwk(balance_microunits)
            return MCPTextResult(
                text=f"{account}: {balance_mrwk} MRWK",
                structured_content={
                    "account": account,
                    "balance_mrwk": balance_mrwk,
                    "balance_microunits": balance_microunits,
                },
            )
        if name == "register_wallet":
            reject_unexpected_args("register_wallet", {"public_key_hex", "label"})
            wallet = register_wallet(
                session,
                public_key_hex=str_arg("public_key_hex"),
                label=optional_str_arg("label") if args.get("label") is not None else None,
            )
            return json.dumps(wallet_to_dict(session, wallet))
        if name == "get_wallet":
            wallet_row = session.get(Wallet, normalized_wallet_address(str_arg("address")))
            if wallet_row is None:
                return "wallet not found"
            return json.dumps(wallet_to_dict(session, wallet_row))
        if name == "submit_wallet_transfer":
            reject_unexpected_args(
                "submit_wallet_transfer",
                {
                    "from_address",
                    "to_address",
                    "amount_mrwk",
                    "nonce",
                    "memo",
                    "signature_hex",
                },
            )
            transfer = submit_wallet_transfer(
                session,
                from_address=str_arg("from_address"),
                to_address=str_arg("to_address"),
                amount_mrwk=str_arg("amount_mrwk"),
                nonce=int_arg("nonce"),
                memo=optional_str_arg("memo"),
                signature_hex=str_arg("signature_hex"),
            )
            return json.dumps(wallet_transfer_to_dict(transfer))
        if name == "get_ledger_entry":
            entry = ledger_entry_to_dict(session, positive_int_arg("sequence"))
            if entry is None:
                return "ledger entry not found"
            return json.dumps(entry)
        if name == "get_proof":
            proof = session.get(Proof, proof_hash_from_path(str_arg("hash")))
            if proof is None:
                return "proof not found"
            try:
                public_payload = json.loads(proof.public_json)
            except (TypeError, json.JSONDecodeError):
                return "invalid proof payload"
            if not isinstance(public_payload, dict):
                return "invalid proof payload"
            return json.dumps(
                {
                    "hash": proof.hash,
                    "kind": proof.kind,
                    "ledger_sequence": proof.ledger_sequence,
                    "bounty_id": proof.bounty_id,
                    "submission_id": proof.submission_id,
                    "created_at": public_utc_timestamp(proof.created_at),
                    "proof": public_payload,
                }
            )
        if name == "submit_work_proof":
            require_known_fields("bounty_id", "issue_number", "repo", "format")
            output_format = output_format_arg()
            has_bounty_id = "bounty_id" in args and args.get("bounty_id") is not None
            has_issue_number = "issue_number" in args and args.get("issue_number") is not None
            repo_selector = optional_repo_selector_arg()
            if has_bounty_id and has_issue_number:
                raise ValueError("use bounty_id or issue_number, not both")
            if repo_selector is not None and not has_issue_number:
                raise ValueError("repo can only be used with issue_number")
            if has_bounty_id:
                bounty = session.get(Bounty, positive_int_arg("bounty_id"))
                if bounty is None:
                    return "bounty not found"
                return (
                    work_proof_guidance_json(bounty, session=session)
                    if output_format == "json"
                    else work_proof_guidance(bounty, session=session)
                )
            if has_issue_number:
                bounty = bounty_by_issue_number(repo_selector)
                if bounty is None:
                    return "bounty not found"
                return (
                    work_proof_guidance_json(bounty, session=session)
                    if output_format == "json"
                    else work_proof_guidance(bounty, session=session)
                )
            if output_format == "json":
                return generic_work_proof_guidance_json()
            return (
                "Open a focused PR or issue, reference the MRWK bounty, include test evidence, "
                "and wait for a maintainer to apply mrwk:accepted."
            )
    raise ValueError("unknown tool")
