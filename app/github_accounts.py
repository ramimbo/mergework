from __future__ import annotations

import re
from typing import Any

GITHUB_LOGIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$")


def github_account_url(login: Any) -> str | None:
    if not isinstance(login, str):
        return None
    clean = login.strip().lower()
    if not GITHUB_LOGIN_RE.fullmatch(clean):
        return None
    return f"/accounts/github:{clean}"
