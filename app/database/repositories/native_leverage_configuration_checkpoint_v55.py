"""Exact actual55 A0 configuration-fence checkpoint; no financial claims."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as catalog,
)
from app.domain import native_leverage_configuration_catalog_v55 as configuration
from app.domain import native_leverage_configuration_enforcement_v55 as enforcement


async def require_initial_configuration_checkpoint_v55(repository, session, scope, row):
    from app.database.repositories import qualification_ledger as ledger

    def need(condition):
        if condition is not True:
            raise ledger.QualificationLedgerError(
                "ledger_configuration_initial_chain_invalid"
            ) from None

    need(
        type(repository) is ledger.QualificationLedgerRepository
        and type(session) is AsyncSession
        and type(row) is ledger.QualificationAccountScope
        and row.account_revision == 0
        and type(row.ledger_revision) is int
        and row.ledger_revision > 0
        and row.claims_json is None
        and row.claims_sha256 is None
        and (row.environment, row.account_id, row.settlement_currency)
        == (scope.environment, scope.account_id, scope.settlement_currency)
        and session.in_transaction()
        and session.get_bind().dialect.name == "postgresql"
    )
    actual = await catalog.verify_actual_issuer_catalog_v2(session)
    need(
        type(actual) is dict
        and actual.get("database_head") == "0055"
        and actual.get("native_baseline_admission") is True
        and actual.get("native_configuration_ddl_sha256") == configuration.DDL_SHA256
        and actual.get("native_configuration_positive_body_sha256")
        == enforcement.BODY_SHA256
    )
    valid = await session.scalar(
        text(
            "SELECT public.qualification_leverage_configuration_initial_ledger_v55(:e,:a,:c,:ar,:lr,:cs)"
        ),
        {
            "e": scope.environment,
            "a": scope.account_id,
            "c": scope.settlement_currency,
            "ar": row.account_revision,
            "lr": row.ledger_revision,
            "cs": row.claims_sha256,
        },
    )
    need(valid is True)
