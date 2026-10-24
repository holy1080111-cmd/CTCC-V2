"""Cold stdlib policy aliases; production database IO is lazy and explicit."""

from app.domain import account_history_partition_catalog_v1 as _policy

AccountHistoryPartitionError = _policy.AccountHistoryPartitionError
expected_account_history_partition_schema = (
    _policy.expected_account_history_partition_schema
)
verify_account_history_partition_catalog = (
    _policy.verify_account_history_partition_catalog
)
canonical = _policy.canonical
digest = _policy.digest


async def read_account_history_partition_catalog(session):
    from app.database.repositories.account_history_partition import (
        read_account_history_partition_catalog as reader,
    )

    return await reader(session)


async def read_account_history_partition_retention(session):
    from app.database.repositories.account_history_partition import (
        read_account_history_partition_retention as reader,
    )

    return await reader(session)
