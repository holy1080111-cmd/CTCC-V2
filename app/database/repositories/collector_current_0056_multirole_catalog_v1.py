"""Read actual DB0056 writer and raw roles through the inherited frame graph."""

from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as current55,
)
from app.domain import collector_current_0055_catalog_v1 as policy
from app.domain import collector_current_0056_inherited_catalog_v1 as actual56
from app.domain import native_database_profile_v1 as profiles


def verify_0056_pair(
    raw,
    writer,
    *,
    raw_name,
    writer_name,
    approved_raw_role_oid,
    approved_writer_role_oid,
    parent_owner_oid,
    profile=profiles.DB0056,
):
    profiles.checked(profile)
    for value in (raw, writer):
        if (
            type(value) is not dict
            or set(value)
            != policy.CATALOG_KEYS
            | ({"g12_0057"} if profile in (profiles.DB0057, profiles.DB0058) else set())
            | ({"native_protective58"} if profile is profiles.DB0058 else set())
            or value["schema"] != profiles.schema(profile, actual56.SCHEMA)
        ):
            current55._deny()
    current55._role(raw["role"], raw_name, expected_oid=approved_raw_role_oid)
    current55._role(writer["role"], writer_name, expected_oid=approved_writer_role_oid)
    if (
        raw_name == writer_name
        or raw["role"]["oid"] == writer["role"]["oid"]
        or type(parent_owner_oid) is not int
        or parent_owner_oid in (raw["role"]["oid"], writer["role"]["oid"])
    ):
        current55._deny()
    qualified_writer = actual56.observe_writer_0056(
        writer, **profiles.arguments(profile)
    )
    if (
        profile in (profiles.DB0057, profiles.DB0058)
        and raw["g12_0057"] != writer["g12_0057"]
    ):
        current55._deny()
    if (
        profile is profiles.DB0058
        and raw["native_protective58"] != writer["native_protective58"]
    ):
        current55._deny()
    for component, parents, schema_name in (
        (
            "issued",
            policy.PARENTS,
            "ctcc.collector_current0054.actual_issued_catalog.v1",
        ),
        (
            "lineage",
            policy.old53.PARENTS,
            "ctcc.collector_current0053.actual_owner_catalog.v1",
        ),
    ):
        left, right = raw[component], writer[component]
        if (
            type(left) is not dict
            or set(left) != policy.BASE_KEYS | policy.old53.EXTRA_KEYS
            or left["owner"] != parent_owner_oid
            or left["actor"] != raw["role"]["oid"]
            or right["owner"] != parent_owner_oid
            or right["actor"] != writer["role"]["oid"]
            or left["table_ok"] is not True
            or left["bad_table_acl"] is not True
            or right["bad_table_acl"] is not False
        ):
            current55._deny()
        for key in policy.BASE_KEYS - {"actor", "bad_table_acl"}:
            if left[key] != right[key]:
                current55._deny()
        actual56._context(
            left,
            role=raw["role"],
            read_only="off",
            isolation="read committed",
            parents=parents,
            schema_name=schema_name,
            **profiles.arguments(profile),
        )
        for key in (
            "table_oid",
            "parent_facts",
            "native_definitions",
            "ri_triggers",
            "custody_incoming",
        ):
            if left[key] != right[key]:
                current55._deny()
        if left["server"]["database"] != right["server"]["database"]:
            current55._deny()
    if (
        type(raw["lineage_base"]) is not dict
        or set(raw["lineage_base"]) != policy.BASE_KEYS
        or raw["lineage_base"]["actor"] != raw["role"]["oid"]
        or raw["lineage_base"]["bad_table_acl"] is not True
    ):
        current55._deny()
    for key in policy.BASE_KEYS - {"actor", "bad_table_acl"}:
        if raw["lineage_base"][key] != writer["lineage_base"][key]:
            current55._deny()
    for key in (
        "table_guards",
        "defaults",
        "acl",
        "custody_internal",
        "scope_parent",
        "operator55",
        "native_submission55",
        "native_closure55",
        "native_terminal55",
        "native_report55",
        "native_close_intent55",
        "native_configuration55",
    ):
        if raw[key] != writer[key]:
            current55._deny()
    # Actor/can_execute differ intentionally: each is checked against its REAL
    # captured role, then only the shared definitions/OIDs and exact ACL roles
    # are compared. No actor projection or whole-object equality is used.
    raw_history = actual56.observe_controlled_history_0056(
        raw["controlled_history55"],
        owner=parent_owner_oid,
        actor=raw["role"]["oid"],
        owner_mode=False,
        reader_role_oid=approved_raw_role_oid,
        **profiles.arguments(profile),
    )
    writer_history = actual56.observe_controlled_history_0056(
        writer["controlled_history55"],
        owner=parent_owner_oid,
        actor=writer["role"]["oid"],
        owner_mode=False,
        **profiles.arguments(profile),
    )
    if raw_history != writer_history:
        current55._deny()
    return {
        "schema": profiles.schema(
            profile, "ctcc.collector_current0056.observed_multirole_catalog.v1"
        ),
        "database_head": profiles.head(profile),
        "actual_separate_roles_observed": True,
        "native_baseline_admission": False,
        "native_definitions_sha256": qualified_writer["native_definitions_sha256"],
        "account_complete": False,
        "execution_authority": False,
    }
