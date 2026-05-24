from __future__ import annotations

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


def validate_deploy_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
    errors.extend(_secret_errors("MERGEWORK_GITHUB_WEBHOOK_SECRET", settings.github_webhook_secret))
    errors.extend(
        _secret_errors("MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET", settings.github_oauth_client_secret)
    )
    errors.extend(_secret_errors("MERGEWORK_ADMIN_TOKEN", settings.admin_token))
    errors.extend(_secret_errors("MERGEWORK_COOKIE_SECRET", settings.cookie_secret))
    if not settings.github_oauth_client_id:
        errors.append("MERGEWORK_GITHUB_OAUTH_CLIENT_ID is required")
    if not settings.admin_logins:
        errors.append("MERGEWORK_ADMIN_LOGINS must list admin GitHub logins")
    if not settings.github_accepted_labelers:
        errors.append("MERGEWORK_GITHUB_ACCEPTED_LABELERS must list maintainer logins")
    parsed_base_url = urlparse(settings.public_base_url)
    if parsed_base_url.scheme != "https":
        errors.append("MERGEWORK_PUBLIC_BASE_URL must use https")
    if not parsed_base_url.netloc:
        errors.append("MERGEWORK_PUBLIC_BASE_URL must include a host")
    if parsed_base_url.path not in ("", "/") or parsed_base_url.params:
        errors.append("MERGEWORK_PUBLIC_BASE_URL must be an origin without a path")
    if parsed_base_url.query or parsed_base_url.fragment:
        errors.append("MERGEWORK_PUBLIC_BASE_URL must not include query or fragment")
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
