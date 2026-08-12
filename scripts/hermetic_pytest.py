from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence

from app.config.settings import Settings


# PostgreSQL integration tests must use the same database as Alembic, and a
# small number of readiness tests use the Compose Redis service.  Every other
# application setting is reset so an operator's deployment profile cannot
# change deterministic test expectations.
PRESERVED_SETTING_ENVIRONMENT_NAMES = frozenset({"DATABASE_URL", "REDIS_URL"})
SETTING_ENVIRONMENT_NAMES = frozenset(
    name.upper() for name in Settings.model_fields
)
SAFE_TEST_ENVIRONMENT = {
    "ENVIRONMENT": "test",
    "TRADING_MODE": "analysis_only",
}
EXECUTION_AUTHORITY_FIELDS = (
    "auto_trade",
    "paper_auto_execution",
    "live_trading",
    "okx_live_allow_order_writes",
    "okx_live_auto_execution",
    "okx_demo_allow_order_writes",
    "okx_demo_auto_execution",
    "okx_demo_soak_allow_execute",
)


def build_hermetic_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Remove deployment settings while retaining test infrastructure URLs."""
    result = {
        name: value
        for name, value in source.items()
        if name.upper() not in SETTING_ENVIRONMENT_NAMES
        and not name.upper().startswith("PYTEST_")
    }

    for preserved_name in PRESERVED_SETTING_ENVIRONMENT_NAMES:
        matching_values = [
            value
            for name, value in source.items()
            if name.upper() == preserved_name
        ]
        if matching_values:
            result[preserved_name] = matching_values[-1]

    result.update(SAFE_TEST_ENVIRONMENT)
    return result


def enabled_execution_authority(settings: Settings) -> tuple[str, ...]:
    return tuple(
        name
        for name in EXECUTION_AUTHORITY_FIELDS
        if bool(getattr(settings, name))
    )


def main(arguments: Sequence[str] | None = None) -> int:
    sanitized = build_hermetic_environment(os.environ)
    os.environ.clear()
    os.environ.update(sanitized)

    settings = Settings(_env_file=None)
    active_authority = enabled_execution_authority(settings)
    if active_authority:
        names = ",".join(active_authority)
        print(f"hermetic pytest refused unsafe defaults: {names}", file=sys.stderr)
        return 2

    print("HERMETIC_PYTEST_ENVIRONMENT=1")
    print("HERMETIC_SETTINGS_PRESERVED=DATABASE_URL,REDIS_URL")
    print("HERMETIC_EXECUTION_AUTHORITY=0")

    import pytest

    return int(pytest.main(list(arguments if arguments is not None else sys.argv[1:])))


if __name__ == "__main__":
    raise SystemExit(main())
