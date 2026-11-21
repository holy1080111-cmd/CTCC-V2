"""Two genuine fresh native schemas; fixed catalog-only source diagnostics.

Existing isolated TLS factories own all setup/cleanup. No retained database,
operator configuration, venue, owner upgrade, or execution authority is used.
"""

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from scripts import native_owner_storage_diagnostic_v1 as diagnostic
from tests.integration import test_native_owner_upgrade_0056_v2_pg as lab55
from tests.integration import test_native_owner_upgrade_0058_pg as lab57

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_actual57_lab = lab55._actual57_lab
_actual56_owner_profile_lab = lab55._actual56_owner_profile_lab
_actual55_v2_lab = lab55._actual55_v2_lab
_owner58_lab = lab57._owner58_lab
_actual57_owner_profile_lab = lab57._actual57_owner_profile_lab


async def _capture(owner, target):
    groups = {}
    async with owner.connect() as connection, connection.begin():
        for statement in (
            "SET TRANSACTION READ ONLY",
            "SET LOCAL search_path TO pg_catalog",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='10000ms'",
        ):
            await connection.exec_driver_sql(statement)
        # No marker/head, deparser, application row, support/operator/AM
        # callback or observed identifier is read/invoked by these statements.
        for name, statement in diagnostic.queries_v1():
            value = await connection.scalar(text(statement))
            assert value is None or type(value) is bool
            groups[name] = value
        number_sql, category_sql = diagnostic.detail_queries_v2()
        rejected_ids = list((await connection.execute(text(number_sql))).scalars())
        assert all(type(value) is int and 0 <= value < 2**32 for value in rejected_ids)
        assert rejected_ids == sorted(set(rejected_ids))
        categories = {}
        for name, statement in category_sql:
            value = await connection.scalar(text(statement))
            assert value is None or type(value) is bool
            categories[name] = value
    report = {
        "schema": diagnostic.COMPONENT_SCHEMA,
        "target": target,
        "predicate_sha256": diagnostic.predicate_sha256_v1(),
        "groups": groups,
        "startup_admission": False,
        "execution_authority": False,
        "schema_admission": False,
        "original_combined_predicate_used": True,
        "rejected_constant_type_ids": rejected_ids,
        "index_rejection_categories": categories,
        "detail_matches_original_groups": (
            groups["expression_constant_type_ids"] is (not bool(rejected_ids))
            and all(type(value) is bool for value in categories.values())
            and groups["public_and_toast_index_metadata_and_predicates"]
            is (not any(categories.values()))
        ),
    }
    leaves = [
        value
        for name, value in groups.items()
        if name not in ("combined", "all_heap_and_indexes", "btree_combined")
    ]
    conjunction = (
        False
        if any(value is False for value in leaves)
        else None
        if any(value is None for value in leaves)
        else True
    )
    report["component_conjunction_matches_original"] = groups["combined"] is conjunction
    directory = os.environ.get(diagnostic.ENV)
    if directory is not None:
        root = Path(directory)
        assert (
            root == Path("/validation-results")
            and root.is_dir()
            and not root.is_symlink()
        )
        identity = json.loads(Path("/ctcc-run-identity.json").read_bytes())
        assert identity["schema"] == diagnostic.SCHEMA
        assert identity["runner_contract"] == diagnostic.CONTRACT
        assert identity["validation_scope"] == diagnostic.SCOPE
        assert identity["suite"] == diagnostic.SUITE
        report.update(
            source_commit=identity["commit"],
            source_tree=identity["tree"],
            source_manifest_sha256=identity["manifest_sha256"],
        )
        with (root / ("native-storage-" + target + ".json")).open(
            "x", encoding="ascii"
        ) as output:
            json.dump(report, output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
    # Data is retained first, but an original FALSE/NULL never becomes PASS
    # even if later observations change. No upgrade or fallback is attempted.
    assert report["component_conjunction_matches_original"] is True
    assert report["detail_matches_original_groups"] is True
    assert groups["combined"] is True and all(
        value is True for value in groups.values()
    ), json.dumps(report, sort_keys=True)


async def test_actual0055_native_storage_catalog_components_are_true(_actual55_v2_lab):
    await _capture(_actual55_v2_lab[0], "0055")


async def test_actual0057_native_storage_catalog_components_are_true(
    _actual57_owner_profile_lab,
):
    await _capture(_actual57_owner_profile_lab[0], "0057")
