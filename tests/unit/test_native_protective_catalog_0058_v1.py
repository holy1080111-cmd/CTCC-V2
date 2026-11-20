"""Declared synthetic physical metadata; no native source or risk authority."""

from copy import deepcopy

import pytest

from app.domain import collector_current_0056_inherited_catalog_v1 as inherited
from app.domain import native_database_profile_v1 as profiles
from app.domain import native_protective_catalog_0058_v1 as catalog
from app.domain import native_protective_hold_schema_v58 as schema
from tests.unit.test_g12_restricted_writer_0057_v1 import _function
from tests.unit.test_native_close_intent_catalog_v55 import _catalog
from tests.unit.test_native_zero_fok_catalog_current55_v1 import _component


def _synthetic():
    original = _catalog()
    indexes = deepcopy(original)
    indexes["indexes"] = []
    for number, (name, version) in enumerate(
        (
            ("qualification_native_protected_hold_once_v58", schema.HOLD_VERSION),
            ("qualification_native_protective_flat_once_v58", schema.TERMINAL_VERSION),
        )
    ):
        index = deepcopy(original["indexes"][0])
        index.update(
            name=name,
            oid=400 + number,
            predicate="(((capture_json)::jsonb ->> 'version'::text) = '"
            + version
            + "'::text)",
            definition=schema.INDEXES[number],
        )
        indexes["indexes"].append(index)
    function = _function(
        schema.FUNCTION,
        schema.PREDICATE_SQL.split("$ctcc$")[1],
        301,
        ["search_path=pg_catalog, pg_temp"],
        public=True,
    )
    function.update(
        owner=100,
        arguments=schema.FUNCTION_IDENTITY,
        returns="boolean",
        acl=[[0, 100, "EXECUTE", False], [100, 100, "EXECUTE", False]],
    )
    return {
        "head": ["0058"],
        "functions": {"schema": catalog.SCHEMA, "functions": [function]},
        "indexes": indexes,
    }, original


def test_exact58_metadata_hashes_predicate_and_both_unique_indexes_without_authority():
    value, original = _synthetic()
    before = deepcopy(value)
    observed = catalog.observe_catalog(value, owner=100, original_close=original)
    assert observed["predicate_body_sha256"] == schema.PREDICATE_BODY_SHA256
    assert len(observed["indexes"]) == 2 and observed["execution_authority"] is False
    assert value == before
    query = inherited.actor_catalog_sql(profiles.DB0058)
    assert "'schema','ctcc.collector_current0058.actual_inherited_catalog.v1'" in query
    assert "'native_protective58'" in query and "FROM public.alembic_version" in query
    assert (
        len(profiles.grant_plan(profiles.DB0058, ("writer", "custody", "frame"))) == 44
    )
    assert (
        profiles.reservation_update_body(profiles.DB0058) == schema.CURRENT_BODY_SHA256
    )
    assert (
        profiles.reservation_update_body(profiles.DB0057) != schema.CURRENT_BODY_SHA256
    )


@pytest.mark.parametrize(
    "fault",
    [
        "head57",
        "old_body",
        "extra_execute",
        "named_args",
        "index_missing",
        "index_duplicate",
        "index_predicate",
        "index_owner",
        "index_invalid",
        "index_extra",
        "parent_changed",
    ],
)
def test_actual58_component_refuses_changed_definition_permission_or_index(fault):
    value, original = _synthetic()
    if fault == "head57":
        value["head"] = ["0057"]
    elif fault == "old_body":
        value["functions"]["functions"][0]["source"] += "\n"
    elif fault == "extra_execute":
        value["functions"]["functions"][0]["acl"].append([999, 100, "EXECUTE", False])
    elif fault == "named_args":
        value["functions"]["functions"][0]["arguments"] = (
            "rid text, closing_at timestamp with time zone"
        )
    elif fault == "index_missing":
        value["indexes"]["indexes"].pop()
    elif fault == "index_duplicate":
        value["indexes"]["indexes"][1] = deepcopy(value["indexes"]["indexes"][0])
    elif fault == "index_predicate":
        value["indexes"]["indexes"][0]["predicate"] = "true"
    elif fault == "index_owner":
        value["indexes"]["indexes"][0]["owner"] = 999
    elif fault == "index_invalid":
        value["indexes"]["indexes"][0]["valid"] = False
    elif fault == "index_extra":
        value["indexes"]["indexes"].append(deepcopy(value["indexes"]["indexes"][0]))
    elif fault == "parent_changed":
        value["indexes"]["tables"][0]["oid"] = 999
    with pytest.raises(ValueError):
        catalog.observe_catalog(value, owner=100, original_close=original)


def test_changed_trigger_is_explicit58_and_legacy55_observer_stays_strict():
    value, owner, configuration = _component()
    from app.domain import collector_current_0055_catalog_v1 as legacy

    old = deepcopy(value)
    with pytest.raises(ValueError):
        catalog.observe_closure58(value, owner=owner, configuration=configuration)
    value["functions"][0].update(
        source=schema.CURRENT_SQL.split("$$")[1], definition=schema.CURRENT_SQL
    )
    assert (
        catalog.observe_closure58(value, owner=owner, configuration=configuration)[
            "function"
        ]
        == schema.CURRENT_SQL
    )
    with pytest.raises(ValueError):
        legacy._closure55(value, owner=owner, configuration=configuration)
    assert (
        legacy._closure55(old, owner=owner, configuration=configuration)["function"]
        != schema.CURRENT_SQL
    )


def test_protective_postflat_binding_is_actual58_and_does_not_replace55_contract():
    from app.domain import native_submission_reporting_schema_v55 as previous

    component, owner, _ = _component()
    row = component["functions"][0]
    row.update(
        name=previous.FUNCTION,
        source=previous.CURRENT_SQL.split("$$")[1],
        definition=previous.CURRENT_SQL,
        config=None,
    )
    legacy = catalog.inherited._submission55([row], owner=owner)
    with pytest.raises(ValueError):
        catalog.observe_submission58([row], owner=owner)
    row.update(
        source=catalog.submission.CURRENT_SQL.split("$$")[1],
        definition=catalog.submission.CURRENT_SQL,
    )
    assert catalog.observe_submission58([row], owner=owner) != legacy
    with pytest.raises(ValueError):
        catalog.inherited._submission55([row], owner=owner)
    original = catalog.submission._public_ddl(catalog.submission.ANCESTOR_SQL)
    current = catalog.submission.CURRENT_SQL
    current = current.replace(catalog.submission._REPORT_GUARD, "", 1)
    start, end = (
        original.index(catalog.submission._START),
        original.index(catalog.submission._END),
    )
    assert (
        current[:start] == original[:start]
        and current[current.index(catalog.submission._END) :] == original[end:]
    )
    assert "ctcc.native_closed_trade_readback.v1" not in catalog.submission._ADDITIONAL
    assert "post_submit_native_original_oco_flat_v58" in current


def test_original_native_kernel_retains_new_catalog_and_schema_code_custody():
    """Compile/replay the original kernel only; no source, DB or Arm is made."""
    from app.trade_qualification import native_controlled_account_source_v6 as source
    from app.trade_qualification import native_protected_hold_lifecycle_v1 as holding
    from app.trade_qualification import native_protected_hold_recovery_v1 as recovery

    source._REQUIRE_SOURCE_KERNEL_V6()
    holding._NATIVE_GUARD()
    recovery._NATIVE_GUARD()
