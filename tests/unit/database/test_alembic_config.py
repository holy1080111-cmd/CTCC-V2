from __future__ import annotations

from alembic.config import Config

from app.database.alembic_config import escape_alembic_config_value


def test_encoded_database_password_is_safe_for_alembic_config_parser() -> None:
    database_url = "postgresql+asyncpg://ctcc:p%40ss%25word@postgres:5432/ctcc"
    config = Config()

    config.set_main_option(
        "sqlalchemy.url", escape_alembic_config_value(database_url)
    )

    assert config.get_main_option("sqlalchemy.url") == database_url
