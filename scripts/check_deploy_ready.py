from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings, validate_deploy_settings
from app.treasury_executor_config import executor_config_from_env


def main() -> int:
    settings = get_settings()
    errors = validate_deploy_settings(settings)
    try:
        executor_config_from_env()
    except ValueError as exc:
        errors.append(str(exc))
    if not errors:
        try:
            from app.oauth_deploy_smoke import validate_oauth_routes_registered

            errors.extend(validate_oauth_routes_registered())
        except ImportError:
            pass
    if errors:
        print("Deploy readiness check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Deploy readiness check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
