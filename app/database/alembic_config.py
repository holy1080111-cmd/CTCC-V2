from __future__ import annotations


def escape_alembic_config_value(value: str) -> str:
    """Escape ConfigParser interpolation markers in Alembic option values."""

    return value.replace("%", "%%")
