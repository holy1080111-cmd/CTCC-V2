"""Pure actual-role predicates; these synthetic catalogs grant no authority."""

import copy

import pytest

from app.database.repositories import (
    account_history_partition_current55_v2 as repository,
)


def _catalog(*, historical_append=False):
    names = tuple(spec[0] for spec in repository.policy.FUNCTION_SPECS)
    return {
        "role": {
            "oid": 201,
            "name": "synthetic_writer",
            "can_login": True,
            **{
                name: False
                for name in (
                    "superuser",
                    "createdb",
                    "createrole",
                    "replication",
                    "bypassrls",
                    "other_membership",
                    "can_create",
                )
            },
        },
        "server": {
            "current_user": "synthetic_writer",
            "session_user": "synthetic_writer",
            "role_setting": "none",
            "timezone": "UTC",
            "search_path": "pg_catalog, public, pg_temp",
            "isolation": "read committed",
            "replication_role": "origin",
            "in_recovery": False,
            "synchronous_commit": "on",
            "fsync": "on",
            "full_page_writes": "on",
        },
        "tables": [{"owner_oid": 101, "table_access": False}],
        "functions": [
            {
                "name": name,
                "owner_oid": 101,
                "can_execute": (
                    historical_append
                    if name == "account_history_partition_v1_append"
                    else name == "account_history_partition_v1_read"
                ),
            }
            for name in names
        ],
    }


@pytest.mark.parametrize("historical_append", (False, True))
def test_new55_role_observes_old_append_without_requiring_or_rewriting_it(
    historical_append,
):
    value = _catalog(historical_append=historical_append)
    before = copy.deepcopy(value)
    assert repository._verify_current55_runtime_role_v2(value) is None
    assert value == before
    if historical_append:
        repository.policy.verify_runtime_role(value)
    else:
        with pytest.raises(
            repository.policy.AccountHistoryPartitionError,
            match="account_history_partition_role_denied",
        ):
            repository.policy.verify_runtime_role(value)


@pytest.mark.parametrize(
    "fault",
    (
        "set_role",
        "wrong_session",
        "wrong_name",
        "no_login",
        "superuser",
        "createdb",
        "createrole",
        "replication",
        "bypassrls",
        "other_membership",
        "can_create",
        "timezone",
        "search_path",
        "isolation",
        "replication_role",
        "in_recovery",
        "synchronous_commit",
        "fsync",
        "full_page_writes",
        "table_owner",
        "table_access",
        "function_owner",
        "missing_old_read",
        "extra_function_execute",
    ),
)
def test_new55_role_preserves_all_actual_actor_session_and_source_restrictions(fault):
    value = _catalog()
    if fault == "set_role":
        value["server"]["role_setting"] = "synthetic_writer"
    elif fault == "wrong_session":
        value["server"]["session_user"] = "different_actor"
    elif fault == "wrong_name":
        value["role"]["name"] = "different_actor"
    elif fault == "no_login":
        value["role"]["can_login"] = False
    elif fault in value["role"]:
        value["role"][fault] = True
    elif fault in ("timezone", "search_path", "isolation", "replication_role"):
        value["server"][fault] = "changed"
    elif fault == "in_recovery":
        value["server"][fault] = True
    elif fault in ("synchronous_commit", "fsync", "full_page_writes"):
        value["server"][fault] = "off"
    elif fault == "table_owner":
        value["tables"][0]["owner_oid"] = 201
    elif fault == "table_access":
        value["tables"][0]["table_access"] = True
    elif fault == "function_owner":
        value["functions"][0]["owner_oid"] = 201
    elif fault == "missing_old_read":
        next(
            row
            for row in value["functions"]
            if row["name"] == "account_history_partition_v1_read"
        )["can_execute"] = False
    elif fault == "extra_function_execute":
        next(
            row
            for row in value["functions"]
            if row["name"]
            not in {
                "account_history_partition_v1_read",
                "account_history_partition_v1_append",
            }
        )["can_execute"] = True
    with pytest.raises(
        repository.policy.AccountHistoryPartitionError,
        match="account_history_partition_role_denied",
    ):
        repository._verify_current55_runtime_role_v2(value)


@pytest.mark.parametrize("value", (None, 0, 1, "false", [], {}))
def test_old_append_privilege_still_requires_an_actual_boolean(value):
    catalog = _catalog()
    next(
        row
        for row in catalog["functions"]
        if row["name"] == "account_history_partition_v1_append"
    )["can_execute"] = value
    with pytest.raises(repository.policy.AccountHistoryPartitionError):
        repository._verify_current55_runtime_role_v2(catalog)
