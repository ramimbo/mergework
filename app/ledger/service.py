from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Bounty,
    LedgerEntry,
    Proof,
    Submission,
    Wallet,
    WalletTransfer,
    utc_now,
)
from app.wallets import (
    WalletError,
    address_from_public_key_hex,
    canonical_wallet_json,
    normalize_signature_hex,
    normalize_wallet_address,
    verify_wallet_signature,
)

TREASURY_ACCOUNT = "treasury:mrwk"
MICRO_UNITS = 1_000_000
GENESIS_SUPPLY_MICRO = 100_000_000 * MICRO_UNITS
SQLITE_INTEGER_MAX = 2**63 - 1
GITHUB_LOGIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
MRWK_AMOUNT_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


class LedgerError(RuntimeError):
    pass


def parse_mrwk_amount(amount: str | int | Decimal) -> int:
    amount_text = str(amount).strip()
    if not MRWK_AMOUNT_RE.fullmatch(amount_text):
        raise LedgerError("invalid MRWK amount")
    try:
        value = Decimal(amount_text)
    except (InvalidOperation, ValueError) as exc:
        raise LedgerError("invalid MRWK amount") from exc
    if not value.is_finite():
        raise LedgerError("invalid MRWK amount")
    if value <= 0:
        raise LedgerError("amount must be positive")
    if value > Decimal(GENESIS_SUPPLY_MICRO) / MICRO_UNITS:
        raise LedgerError("amount exceeds fixed supply")
    scaled = value * MICRO_UNITS
    if scaled != scaled.to_integral_value():
        raise LedgerError("MRWK supports at most 6 decimal places")
    try:
        microunits = int(scaled)
    except (OverflowError, ValueError) as exc:
        raise LedgerError("invalid MRWK amount") from exc
    return microunits


def format_mrwk(microunits: int) -> str:
    whole, frac = divmod(microunits, MICRO_UNITS)
    if frac == 0:
        return str(whole)
    return f"{whole}.{frac:06d}".rstrip("0")


def canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def validate_public_url(url: str) -> str:
    if not isinstance(url, str):
        raise LedgerError("URL must be a string")
    if CONTROL_CHAR_RE.search(url):
        raise LedgerError("URL must not contain control characters")
    clean = url.strip()
    if len(clean) > 500:
        raise LedgerError("URL is too long")
    if any(char.isspace() for char in clean):
        raise LedgerError("URL must not contain whitespace")
    try:
        parsed = urlparse(clean)
        has_empty_port = parsed.port is None and parsed.netloc.endswith(":")
        hostname = parsed.hostname
    except ValueError as exc:
        raise LedgerError("URL must include a valid host") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LedgerError("URL must use http or https")
    if not hostname or has_empty_port:
        raise LedgerError("URL must include a valid host")
    if parsed.username is not None or parsed.password is not None:
        raise LedgerError("URL must not include credentials")
    if hostname.lower() == "localhost":
        raise LedgerError("URL must use a public host")
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if ip.is_multicast or not ip.is_global:
            raise LedgerError("URL must use a public host")
    return clean


def public_url_or_none(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return validate_public_url(url)
    except LedgerError:
        return None


def reserve_account_for_bounty(bounty_id: int) -> str:
    return f"reserve:bounty:{bounty_id}"


def _normalize_github_login(github_login: str) -> str:
    if not isinstance(github_login, str):
        raise LedgerError("github login must be a string")
    normalized = github_login.strip().lower()
    if not GITHUB_LOGIN_RE.fullmatch(normalized):
        raise LedgerError("invalid github login")
    return normalized


def resolve_payout_account(session: Session, to_account: str) -> str:
    clean = _clean_required_text(to_account, "to_account", 128)
    lower = clean.lower()
    if lower.startswith("github:"):
        login = _normalize_github_login(clean.split(":", 1)[1])
        linked_wallet = linked_wallet_for_github(session, login)
        return linked_wallet.address if linked_wallet is not None else f"github:{login}"
    if lower.startswith("mrwk1"):
        try:
            address = normalize_wallet_address(clean)
        except WalletError as exc:
            raise LedgerError(str(exc)) from exc
        if session.get(Wallet, address) is None:
            raise LedgerError("wallet not found")
        return address
    raise LedgerError("to_account must be a github:<login> account or registered mrwk1 wallet")


def _clean_optional_text(value: str | None, field: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LedgerError(f"{field} must be a string")
    if CONTROL_CHAR_RE.search(value):
        raise LedgerError(f"{field} must not contain control characters")
    clean = value.strip()
    if len(clean) > max_length:
        raise LedgerError(f"{field} is too long")
    return clean or None


def _clean_required_text(value: str, field: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise LedgerError(f"{field} must be a string")
    if CONTROL_CHAR_RE.search(value):
        raise LedgerError(f"{field} must not contain control characters")
    clean = value.strip()
    if not clean:
        raise LedgerError(f"{field} is required")
    if len(clean) > max_length:
        raise LedgerError(f"{field} is too long")
    return clean


def _normalize_repo_name(repo: str) -> str:
    return _clean_required_text(repo, "repo", 200).lower()


def _clean_optional_account_id(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    return _clean_required_text(value, field, 128)


def _clean_proof_metadata(verifier_result: dict[str, Any]) -> dict[str, Any]:
    clean = dict(verifier_result)
    for key, value in clean.items():
        if isinstance(value, str) and CONTROL_CHAR_RE.search(value):
            raise LedgerError(f"verifier_result.{key} must not contain control characters")
    try:
        canonical_json(clean)
    except (TypeError, ValueError) as exc:
        raise LedgerError("verifier_result must be JSON serializable") from exc
    return clean


def wallet_transfer_payload(
    *,
    from_address: str,
    to_address: str,
    amount_microunits: int,
    nonce: int,
    memo: str,
) -> dict[str, object]:
    return {
        "type": "mrwk_transfer_v1",
        "from_address": normalize_wallet_address(from_address),
        "to_address": normalize_wallet_address(to_address),
        "amount_microunits": amount_microunits,
        "nonce": nonce,
        "memo": memo,
    }


def wallet_link_payload(*, address: str, github_login: str, nonce: int) -> dict[str, object]:
    return {
        "type": "mrwk_link_github_v1",
        "address": normalize_wallet_address(address),
        "github_login": _normalize_github_login(github_login),
        "nonce": nonce,
    }


def wallet_claim_payload(*, address: str, github_login: str, nonce: int) -> dict[str, object]:
    return {
        "type": "mrwk_claim_github_v1",
        "address": normalize_wallet_address(address),
        "github_login": _normalize_github_login(github_login),
        "nonce": nonce,
    }


def _canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC).replace(tzinfo=None)
    return value.isoformat(timespec="microseconds") + "Z"


def _entry_payload(entry: LedgerEntry) -> dict[str, Any]:
    return {
        "sequence": entry.sequence,
        "entry_type": entry.entry_type,
        "from_account": entry.from_account,
        "to_account": entry.to_account,
        "amount_microunits": entry.amount_microunits,
        "reference": entry.reference,
        "previous_hash": entry.previous_hash,
        "created_at": _canonical_timestamp(entry.created_at),
    }


def compute_entry_hash(entry: LedgerEntry) -> str:
    return hashlib.sha256(canonical_json(_entry_payload(entry)).encode()).hexdigest()


def ensure_account(session: Session, account_id: str) -> Account:
    account = session.get(Account, account_id)
    if account is not None:
        return account
    github_login = account_id.removeprefix("github:") if account_id.startswith("github:") else None
    account = Account(id=account_id, github_login=github_login, display_name=github_login)
    session.add(account)
    session.flush()
    return account


def register_wallet(
    session: Session,
    *,
    public_key_hex: str,
    label: str | None = None,
    github_login: str | None = None,
) -> Wallet:
    try:
        address = address_from_public_key_hex(public_key_hex)
    except WalletError as exc:
        raise LedgerError(str(exc)) from exc
    public_key = public_key_hex.strip().lower()
    existing = session.get(Wallet, address)
    if github_login is None or github_login == "":
        normalized_login = None
    else:
        normalized_login = _normalize_github_login(github_login)
    clean_label = _clean_optional_text(label, "wallet label", 160)
    if normalized_login:
        linked = session.scalar(
            select(Wallet).where(Wallet.github_login == normalized_login, Wallet.address != address)
        )
        if linked is not None:
            raise LedgerError("github login already linked")
    if existing is not None:
        if existing.public_key_hex != public_key:
            raise LedgerError("wallet address public key mismatch")
        if label is not None:
            existing.label = clean_label
        if normalized_login is not None:
            existing.github_login = normalized_login
            ensure_account(session, f"github:{normalized_login}")
        ensure_account(session, address)
        return existing
    wallet = Wallet(
        address=address,
        public_key_hex=public_key,
        label=clean_label,
        github_login=normalized_login,
        nonce=0,
    )
    session.add(wallet)
    ensure_account(session, address)
    if normalized_login is not None:
        ensure_account(session, f"github:{normalized_login}")
    session.flush()
    return wallet


def linked_wallet_for_github(session: Session, github_login: str) -> Wallet | None:
    return session.scalar(
        select(Wallet).where(Wallet.github_login == _normalize_github_login(github_login)).limit(1)
    )


def _wallet_for_update(session: Session, address: str) -> Wallet:
    try:
        normalized = normalize_wallet_address(address)
    except WalletError as exc:
        raise LedgerError(str(exc)) from exc
    wallet = session.get(Wallet, normalized)
    if wallet is None:
        raise LedgerError("wallet not found")
    return wallet


def _verify_wallet_payload(
    wallet: Wallet,
    *,
    payload: dict[str, object],
    nonce: int,
    signature_hex: str,
) -> str:
    if isinstance(nonce, bool) or not isinstance(nonce, int):
        raise LedgerError("nonce must be an integer")
    if nonce != wallet.nonce + 1:
        raise LedgerError("invalid nonce")
    try:
        signature = normalize_signature_hex(signature_hex)
    except WalletError as exc:
        raise LedgerError(str(exc)) from exc
    if not verify_wallet_signature(
        public_key_hex=wallet.public_key_hex, payload=payload, signature_hex=signature
    ):
        raise LedgerError("invalid signature")
    return signature


def _next_sequence(session: Session) -> int:
    current = session.scalar(select(func.max(LedgerEntry.sequence)))
    return int(current or 0) + 1


def _previous_hash(session: Session) -> str:
    entry = session.scalar(select(LedgerEntry).order_by(LedgerEntry.sequence.desc()).limit(1))
    return entry.entry_hash if entry else "0" * 64


def add_ledger_entry(
    session: Session,
    *,
    entry_type: str,
    from_account: str | None,
    to_account: str | None,
    amount_microunits: int,
    reference: str,
) -> LedgerEntry:
    if isinstance(amount_microunits, bool) or not isinstance(amount_microunits, int):
        raise LedgerError("amount_microunits must be an integer")
    if amount_microunits < 0:
        raise LedgerError("ledger amount cannot be negative")
    clean_entry_type = _clean_required_text(entry_type, "entry_type", 40)
    clean_from_account = _clean_optional_account_id(from_account, "from_account")
    clean_to_account = _clean_optional_account_id(to_account, "to_account")
    clean_reference = _clean_required_text(reference, "reference", 500)
    if clean_from_account:
        ensure_account(session, clean_from_account)
    if clean_to_account:
        ensure_account(session, clean_to_account)
    entry = LedgerEntry(
        sequence=_next_sequence(session),
        entry_type=clean_entry_type,
        from_account=clean_from_account,
        to_account=clean_to_account,
        amount_microunits=amount_microunits,
        reference=clean_reference,
        previous_hash=_previous_hash(session),
        entry_hash="",
        created_at=utc_now(),
    )
    entry.entry_hash = compute_entry_hash(entry)
    session.add(entry)
    session.flush()
    return entry


def ensure_genesis(session: Session) -> LedgerEntry:
    existing = session.get(LedgerEntry, 1)
    if existing is not None:
        return existing
    ensure_account(session, TREASURY_ACCOUNT)
    return add_ledger_entry(
        session,
        entry_type="genesis",
        from_account=None,
        to_account=TREASURY_ACCOUNT,
        amount_microunits=GENESIS_SUPPLY_MICRO,
        reference="mergework-genesis",
    )


def create_bounty(
    session: Session,
    *,
    repo: str,
    issue_number: int,
    issue_url: str,
    title: str,
    reward_mrwk: str,
    max_awards: int = 1,
    acceptance: str,
) -> Bounty:
    ensure_genesis(session)
    reward = parse_mrwk_amount(reward_mrwk)
    if issue_number <= 0:
        raise LedgerError("issue_number must be positive")
    if max_awards <= 0:
        raise LedgerError("max_awards must be positive")
    if max_awards > 1_000:
        raise LedgerError("max_awards is too large")
    reserved = reward * max_awards
    clean_repo = _normalize_repo_name(repo)
    if issue_number > SQLITE_INTEGER_MAX:
        raise LedgerError("issue_number is too large")
    existing_bounty = find_bounty_by_issue(session, clean_repo, issue_number)
    if existing_bounty is not None:
        raise LedgerError("bounty already exists for issue")
    clean_issue_url = validate_public_url(issue_url)
    clean_title = _clean_required_text(title, "title", 300)
    clean_acceptance = _clean_required_text(acceptance, "acceptance", 5_000)
    if get_balance(session, TREASURY_ACCOUNT) < reserved:
        raise LedgerError("treasury balance too low")
    bounty = Bounty(
        repo=clean_repo,
        issue_number=issue_number,
        issue_url=clean_issue_url,
        title=clean_title,
        reward_microunits=reward,
        reserved_microunits=reserved,
        max_awards=max_awards,
        awards_paid=0,
        status="open",
        acceptance=clean_acceptance,
    )
    session.add(bounty)
    session.flush()
    add_ledger_entry(
        session,
        entry_type="bounty_reserve",
        from_account=TREASURY_ACCOUNT,
        to_account=reserve_account_for_bounty(bounty.id),
        amount_microunits=reserved,
        reference=clean_issue_url,
    )
    return bounty


def find_bounty_by_issue(session: Session, repo: str, issue_number: int) -> Bounty | None:
    clean_repo = _normalize_repo_name(repo)
    return session.scalar(
        select(Bounty)
        .where(func.lower(Bounty.repo) == clean_repo, Bounty.issue_number == issue_number)
        .limit(1)
    )


def get_balance(session: Session, account_id: str) -> int:
    credits = session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_microunits), 0)).where(
            LedgerEntry.to_account == account_id
        )
    )
    debits = session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_microunits), 0)).where(
            LedgerEntry.from_account == account_id
        )
    )
    return int(credits or 0) - int(debits or 0)


def submit_wallet_transfer(
    session: Session,
    *,
    from_address: str,
    to_address: str,
    amount_mrwk: str,
    nonce: int,
    memo: str,
    signature_hex: str,
) -> WalletTransfer:
    ensure_genesis(session)
    sender = _wallet_for_update(session, from_address)
    receiver = _wallet_for_update(session, to_address)
    if sender.address == receiver.address:
        raise LedgerError("sender and receiver must be different")
    amount = parse_mrwk_amount(amount_mrwk)
    if CONTROL_CHAR_RE.search(memo):
        raise LedgerError("memo must not contain control characters")
    clean_memo = memo.strip()
    if len(clean_memo) > 240:
        raise LedgerError("memo is too long")
    if get_balance(session, sender.address) < amount:
        raise LedgerError("insufficient balance")
    payload = wallet_transfer_payload(
        from_address=sender.address,
        to_address=receiver.address,
        amount_microunits=amount,
        nonce=nonce,
        memo=clean_memo,
    )
    signature = _verify_wallet_payload(
        sender, payload=payload, nonce=nonce, signature_hex=signature_hex
    )
    payload_json = canonical_wallet_json(payload)
    transfer_hash = hashlib.sha256(f"{payload_json}.{signature}".encode()).hexdigest()
    if session.get(WalletTransfer, transfer_hash) is not None:
        raise LedgerError("transfer already exists")
    ledger_entry = add_ledger_entry(
        session,
        entry_type="wallet_transfer",
        from_account=sender.address,
        to_account=receiver.address,
        amount_microunits=amount,
        reference=f"transfer:{transfer_hash}",
    )
    sender.nonce = nonce
    transfer = WalletTransfer(
        hash=transfer_hash,
        ledger_sequence=ledger_entry.sequence,
        from_address=sender.address,
        to_address=receiver.address,
        amount_microunits=amount,
        nonce=nonce,
        memo=clean_memo,
        signature_hex=signature,
        payload_json=payload_json,
    )
    session.add(transfer)
    session.flush()
    return transfer


def link_wallet_to_github(
    session: Session,
    *,
    address: str,
    github_login: str,
    nonce: int,
    signature_hex: str,
) -> Wallet:
    wallet = _wallet_for_update(session, address)
    normalized_login = _normalize_github_login(github_login)
    linked = linked_wallet_for_github(session, normalized_login)
    if linked is not None and linked.address != wallet.address:
        raise LedgerError("github login already linked")
    payload = wallet_link_payload(
        address=wallet.address, github_login=normalized_login, nonce=nonce
    )
    _verify_wallet_payload(wallet, payload=payload, nonce=nonce, signature_hex=signature_hex)
    wallet.github_login = normalized_login
    wallet.nonce = nonce
    ensure_account(session, f"github:{normalized_login}")
    session.flush()
    return wallet


def submit_github_claim(
    session: Session,
    *,
    address: str,
    github_login: str,
    nonce: int,
    signature_hex: str,
) -> LedgerEntry:
    ensure_genesis(session)
    wallet = _wallet_for_update(session, address)
    normalized_login = _normalize_github_login(github_login)
    if wallet.github_login != normalized_login:
        raise LedgerError("wallet is not linked to github login")
    payload = wallet_claim_payload(
        address=wallet.address, github_login=normalized_login, nonce=nonce
    )
    _verify_wallet_payload(wallet, payload=payload, nonce=nonce, signature_hex=signature_hex)
    github_account = f"github:{normalized_login}"
    amount = get_balance(session, github_account)
    if amount <= 0:
        raise LedgerError("no github balance to claim")
    ledger_entry = add_ledger_entry(
        session,
        entry_type="github_claim",
        from_account=github_account,
        to_account=wallet.address,
        amount_microunits=amount,
        reference=f"github-claim:{normalized_login}:{wallet.address}:{nonce}",
    )
    wallet.nonce = nonce
    session.flush()
    return ledger_entry


def pay_bounty(
    session: Session,
    *,
    bounty_id: int,
    to_account: str,
    submission_url: str,
    accepted_by: str,
    verifier_result: dict[str, Any],
) -> Proof:
    ensure_genesis(session)
    bounty = session.get(Bounty, bounty_id)
    if bounty is None:
        raise LedgerError("bounty not found")
    if bounty.awards_paid >= bounty.max_awards:
        raise LedgerError("bounty already paid")
    if bounty.status != "open":
        raise LedgerError("bounty is not open")
    clean_accepted_by = _clean_required_text(accepted_by, "accepted_by", 80)
    clean_verifier_result = _clean_proof_metadata(verifier_result)
    if "accepted_by" in clean_verifier_result:
        clean_verifier_result["accepted_by"] = clean_accepted_by
    clean_to_account = _clean_required_text(to_account, "to_account", 128)
    clean_submission_url = validate_public_url(submission_url)
    existing_submission = session.scalar(
        select(Submission)
        .where(Submission.bounty_id == bounty.id, Submission.url == clean_submission_url)
        .limit(1)
    )
    if existing_submission is not None:
        raise LedgerError("submission already paid")
    reserve_account = reserve_account_for_bounty(bounty.id)
    if get_balance(session, reserve_account) < bounty.reward_microunits:
        raise LedgerError("bounty reserve balance too low")
    claimed = session.execute(
        update(Bounty)
        .where(Bounty.id == bounty.id, Bounty.awards_paid < Bounty.max_awards)
        .values(
            awards_paid=Bounty.awards_paid + 1,
            status=case(
                (Bounty.awards_paid + 1 >= Bounty.max_awards, "paid"),
                else_="open",
            ),
        )
    )
    if claimed.rowcount != 1:
        raise LedgerError("bounty already paid")
    session.refresh(bounty)

    submission = Submission(
        bounty_id=bounty.id,
        submitter_account=clean_to_account,
        url=clean_submission_url,
        status="accepted",
        verifier_result=canonical_json(clean_verifier_result),
    )
    session.add(submission)
    try:
        session.flush()
    except IntegrityError as exc:
        raise LedgerError("submission already paid") from exc
    ledger_entry = add_ledger_entry(
        session,
        entry_type="bounty_payment",
        from_account=reserve_account,
        to_account=clean_to_account,
        amount_microunits=bounty.reward_microunits,
        reference=clean_submission_url,
    )
    proof_payload = {
        "kind": "bounty_payment",
        "bounty_id": bounty.id,
        "repo": bounty.repo,
        "issue_number": bounty.issue_number,
        "submission_url": clean_submission_url,
        "accepted_by": clean_accepted_by,
        "to_account": clean_to_account,
        "amount_mrwk": format_mrwk(bounty.reward_microunits),
        "ledger_sequence": ledger_entry.sequence,
        "ledger_hash": ledger_entry.entry_hash,
        "verifier_result": clean_verifier_result,
    }
    proof_hash = hashlib.sha256(canonical_json(proof_payload).encode()).hexdigest()
    proof = Proof(
        hash=proof_hash,
        ledger_sequence=ledger_entry.sequence,
        bounty_id=bounty.id,
        submission_id=submission.id,
        kind="bounty_payment",
        public_json=canonical_json(proof_payload),
    )
    session.add(proof)
    session.flush()
    return proof


def close_bounty(
    session: Session,
    *,
    bounty_id: int,
    closed_by: str,
    reference: str | None = None,
) -> LedgerEntry | None:
    ensure_genesis(session)
    bounty = session.get(Bounty, bounty_id)
    if bounty is None:
        raise LedgerError("bounty not found")
    if bounty.status != "open":
        raise LedgerError("bounty is not open")
    _clean_required_text(closed_by, "closed_by", 80)
    clean_reference = validate_public_url(reference or bounty.issue_url)
    claimed = session.execute(
        update(Bounty)
        .where(Bounty.id == bounty.id, Bounty.status == "open")
        .values(status="closed")
    )
    if claimed.rowcount != 1:
        raise LedgerError("bounty is not open")
    session.refresh(bounty)
    reserve_account = reserve_account_for_bounty(bounty.id)
    release_amount = get_balance(session, reserve_account)
    if release_amount <= 0:
        return None
    return add_ledger_entry(
        session,
        entry_type="bounty_release",
        from_account=reserve_account,
        to_account=TREASURY_ACCOUNT,
        amount_microunits=release_amount,
        reference=clean_reference,
    )


def verify_supply_conservation(session: Session) -> bool:
    credits = session.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_microunits), 0)))
    debits = session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_microunits), 0)).where(
            LedgerEntry.from_account.is_not(None)
        )
    )
    return int(credits or 0) - int(debits or 0) == GENESIS_SUPPLY_MICRO


def verify_hash_chain(session: Session) -> bool:
    entries = list(session.scalars(select(LedgerEntry).order_by(LedgerEntry.sequence)).all())
    previous = "0" * 64
    expected_sequence = 1
    for entry in entries:
        if entry.sequence != expected_sequence:
            return False
        if entry.previous_hash != previous:
            return False
        if entry.entry_hash != compute_entry_hash(entry):
            return False
        previous = entry.entry_hash
        expected_sequence += 1
    return True
