from __future__ import annotations

import ipaddress
import os
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

WEAK_OAUTH_CLIENT_ID_VALUES = WEAK_SECRET_VALUES | {
    "client-id",
    "clientid",
    "client_id",
    "dummy",
    "example",
    "github-client-id",
    "github-oauth-client-id",
    "oauth-client-id",
    "placeholder",
    "your-client-id",
}


def _csv_env(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        item.strip().lower() for item in os.environ.get(name, default).split(",") if item.strip()
    )


def _secret_errors(name: str, value: str) -> list[str]:
    if not value:
        return [f"{name} is required"]
    if len(value) < 32 or value.strip().lower() in WEAK_SECRET_VALUES:
        return [f"{name} must be at least 32 characters"]
    if len(set(value)) < 12:
        return [f"{name} must look randomly generated"]
    return []


def _github_oauth_client_id_errors(value: str) -> list[str]:
    stripped = value.strip()
    if not stripped:
        return ["MERGEWORK_GITHUB_OAUTH_CLIENT_ID is required"]
    if stripped.lower() in WEAK_OAUTH_CLIENT_ID_VALUES:
        return ["MERGEWORK_GITHUB_OAUTH_CLIENT_ID must not use a placeholder value"]
    return []


def validate_deploy_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
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
    errors.extend(_github_oauth_client_id_errors(settings.github_oauth_client_id))
    if not settings.admin_logins:
        errors.append("MERGEWORK_ADMIN_LOGINS must list admin GitHub logins")
    if not settings.github_accepted_labelers:
        errors.append("MERGEWORK_GITHUB_ACCEPTED_LABELERS must list maintainer logins")
    if settings.admin_logins and settings.github_accepted_labelers:
        non_admin_labelers = sorted(
            set(settings.github_accepted_labelers) - set(settings.admin_logins)
        )
        if non_admin_labelers:
            errors.append(
                "MERGEWORK_GITHUB_ACCEPTED_LABELERS must be included in MERGEWORK_ADMIN_LOGINS"
            )
    parsed_base_url = urlparse(settings.public_base_url)
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
    if parsed_base_url.hostname:
        try:
            ip_address = ipaddress.ip_address(parsed_base_url.hostname)
        except ValueError:
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
