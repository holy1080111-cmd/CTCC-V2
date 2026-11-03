"""Actual captured writer54 catalog query; no settings, factory or role override."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import collector_current_0054_catalog_v1 as policy
from app.domain.source_primitives import canonical, sha


async def verify_actual_issuer_catalog_v1(session):
    try:
        if (
            type(session) is not AsyncSession
            or session.get_bind().dialect.name != "postgresql"
        ):
            raise policy.Current0054CatalogError(
                "current0054_actual_catalog_unavailable"
            ) from None
        value = (await session.execute(text(policy.ACTOR_CATALOG_SQL))).scalar_one()
        observed = policy.verify_actual_writer_catalog(value)
        return {
            **observed,
            "catalog_sha256": sha(canonical(value)),
            "account_complete": False,
            "execution_authority": False,
        }
    except policy.Current0054CatalogError:
        raise
    except Exception:  # noqa: BLE001 -- sanitize unexpected DB/catalog failures
        raise policy.Current0054CatalogError(
            "current0054_actual_catalog_unavailable"
        ) from None
