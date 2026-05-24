from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Settings:
    database_url: str
    public_base_url: str
    github_webhook_secret: str
    github_oauth_client_id: str
    github_oauth_client_secret: str
    admin_logins: tuple[str, ...]
    admin_token: str
    cookie_secret: str
    github_accepted_labelers: tuple[str, ...]


WEAK_SECRET_VALUES = {
    "change-me",
    "changeme",
    "secret",
    "test",
    "password",
    "admin",
    "mergework",
}
GITHUB_LOGIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$")

DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def _csv_env(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        item.strip().lower() for item in os.environ.get(name, default).split(",") if item.strip()
    )


def _secret_errors(name: str, value: str) -> list[str]:
    if not value:
        return [f"{name} is required"]
    if value != value.strip():
        return [f"{name} must not include leading or trailing whitespace"]
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return [f"{name} must not include control characters"]
    if len(value) < 32 or value.strip().lower() in WEAK_SECRET_VALUES:
        return [f"{name} must be at least 32 characters"]
    if len(set(value)) < 12:
        return [f"{name} must look randomly generated"]
    return []


def _required_env_value_errors(name: str, value: str) -> list[str]:
    if not value.strip():
        return [f"{name} is required"]
    errors = []
    if value != value.strip():
        errors.append(f"{name} must not include leading or trailing whitespace")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        errors.append(f"{name} must not include control characters")
    return errors


def _invalid_github_logins(logins: tuple[str, ...]) -> list[str]:
    return sorted({login for login in logins if not GITHUB_LOGIN_RE.fullmatch(login)})


def _is_valid_dns_hostname(hostname: str) -> bool:
    if len(hostname) > 253:
        return False
    labels = hostname.lower().split(".")
    return all(DNS_LABEL_RE.fullmatch(label) for label in labels)


def _sqlite_database_errors(database_url: str) -> list[str]:
    parsed = urlparse(database_url)
    if not parsed.scheme.startswith("sqlite"):
        return []

    sqlite_path = parsed.path
    is_memory = database_url == "sqlite:///:memory:" or sqlite_path == "/:memory:"
    if is_memory or sqlite_path in ("", "/", "//"):
        return ["MERGEWORK_DATABASE_URL must use a persistent sqlite file"]

    is_posix_absolute = sqlite_path.startswith("//") and len(sqlite_path) > 2
    is_windows_absolute = len(sqlite_path) > 3 and sqlite_path[0] == "/" and sqlite_path[2] == ":"
    if not (is_posix_absolute or is_windows_absolute):
        return ["MERGEWORK_DATABASE_URL sqlite paths must be absolute for deploys"]

    return []


def validate_deploy_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
    errors.extend(_sqlite_database_errors(settings.database_url))
    errors.extend(_secret_errors("MERGEWORK_GITHUB_WEBHOOK_SECRET", settings.github_webhook_secret))
    errors.extend(
        _secret_errors("MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET", settings.github_oauth_client_secret)
    )
    errors.extend(_secret_errors("MERGEWORK_ADMIN_TOKEN", settings.admin_token))
    errors.extend(_secret_errors("MERGEWORK_COOKIE_SECRET", settings.cookie_secret))
    deploy_secrets = [
        settings.github_webhook_secret,
        settings.github_oauth_client_secret,
        settings.admin_token,
        settings.cookie_secret,
    ]
    present_secrets = [secret for secret in deploy_secrets if secret]
    if len(set(present_secrets)) != len(present_secrets):
        errors.append("deploy secrets must use distinct values")
    errors.extend(
        _required_env_value_errors(
            "MERGEWORK_GITHUB_OAUTH_CLIENT_ID", settings.github_oauth_client_id
        )
    )
    if not settings.admin_logins:
        errors.append("MERGEWORK_ADMIN_LOGINS must list admin GitHub logins")
    else:
        if len(set(settings.admin_logins)) != len(settings.admin_logins):
            errors.append("MERGEWORK_ADMIN_LOGINS must not include duplicate logins")
        if _invalid_github_logins(settings.admin_logins):
            errors.append("MERGEWORK_ADMIN_LOGINS must contain valid GitHub logins")
    if not settings.github_accepted_labelers:
        errors.append("MERGEWORK_GITHUB_ACCEPTED_LABELERS must list maintainer logins")
    else:
        if len(set(settings.github_accepted_labelers)) != len(settings.github_accepted_labelers):
            errors.append("MERGEWORK_GITHUB_ACCEPTED_LABELERS must not include duplicate logins")
        if _invalid_github_logins(settings.github_accepted_labelers):
            errors.append("MERGEWORK_GITHUB_ACCEPTED_LABELERS must contain valid GitHub logins")
    if settings.admin_logins and settings.github_accepted_labelers:
        non_admin_labelers = sorted(
            set(settings.github_accepted_labelers) - set(settings.admin_logins)
        )
        if non_admin_labelers:
            errors.append(
                "MERGEWORK_GITHUB_ACCEPTED_LABELERS must be included in MERGEWORK_ADMIN_LOGINS"
            )
    errors.extend(_required_env_value_errors("MERGEWORK_PUBLIC_BASE_URL", settings.public_base_url))
    try:
        parsed_base_url = urlparse(settings.public_base_url)
    except ValueError:
        errors.append("MERGEWORK_PUBLIC_BASE_URL must include a valid host")
        return errors
    authority = parsed_base_url.netloc.rsplit("@", 1)[-1]
    has_bracketed_host = authority.startswith("[")
    if parsed_base_url.scheme != "https":
        errors.append("MERGEWORK_PUBLIC_BASE_URL must use https")
    if not parsed_base_url.netloc:
        errors.append("MERGEWORK_PUBLIC_BASE_URL must include a host")
    if parsed_base_url.path not in ("", "/") or parsed_base_url.params:
        errors.append("MERGEWORK_PUBLIC_BASE_URL must be an origin without a path")
    if parsed_base_url.query or parsed_base_url.fragment:
        errors.append("MERGEWORK_PUBLIC_BASE_URL must not include query or fragment")
    if parsed_base_url.username or parsed_base_url.password:
        errors.append("MERGEWORK_PUBLIC_BASE_URL must not include userinfo")
    try:
        has_empty_port = parsed_base_url.port is None and parsed_base_url.netloc.endswith(":")
    except ValueError:
        errors.append("MERGEWORK_PUBLIC_BASE_URL must include a valid host")
    else:
        if has_empty_port:
            errors.append("MERGEWORK_PUBLIC_BASE_URL must include a valid host")
    if not parsed_base_url.hostname:
        errors.append("MERGEWORK_PUBLIC_BASE_URL must include a valid host")
    else:
        try:
            ip_address = ipaddress.ip_address(parsed_base_url.hostname)
        except ValueError:
            if has_bracketed_host or not _is_valid_dns_hostname(parsed_base_url.hostname):
                errors.append("MERGEWORK_PUBLIC_BASE_URL must include a valid host")
            is_loopback = parsed_base_url.hostname.lower() == "localhost"
            is_private_or_link_local = False
        else:
            is_loopback = ip_address.is_loopback
            is_private_or_link_local = ip_address.is_private or ip_address.is_link_local
        if is_loopback:
            errors.append("MERGEWORK_PUBLIC_BASE_URL must not use a loopback host")
        elif is_private_or_link_local:
            errors.append("MERGEWORK_PUBLIC_BASE_URL must not use a private or link-local host")
    return errors


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("MERGEWORK_DATABASE_URL", "sqlite:///./mergework.sqlite3"),
        public_base_url=os.environ.get("MERGEWORK_PUBLIC_BASE_URL", "https://mrwk.ltclab.site"),
        github_webhook_secret=os.environ.get("MERGEWORK_GITHUB_WEBHOOK_SECRET", ""),
        github_oauth_client_id=os.environ.get("MERGEWORK_GITHUB_OAUTH_CLIENT_ID", ""),
        github_oauth_client_secret=os.environ.get("MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET", ""),
        admin_logins=_csv_env("MERGEWORK_ADMIN_LOGINS"),
        admin_token=os.environ.get("MERGEWORK_ADMIN_TOKEN", ""),
        cookie_secret=os.environ.get("MERGEWORK_COOKIE_SECRET", ""),
        github_accepted_labelers=_csv_env("MERGEWORK_GITHUB_ACCEPTED_LABELERS"),
    )
