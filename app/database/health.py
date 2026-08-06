from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database.session import engine


async def check_database() -> tuple[bool, str]:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True, "database reachable"
    except SQLAlchemyError as exc:
        return False, f"database unavailable: {exc.__class__.__name__}"
    except Exception as exc:
        return False, f"database unavailable: {exc.__class__.__name__}"
