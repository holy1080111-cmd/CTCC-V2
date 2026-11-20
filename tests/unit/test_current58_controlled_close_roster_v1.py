"""Source/roster/model contracts only; no database, venue or execution proof."""

import ast
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

import pytest

from app.database import models
from app.database.base import Base
from app.domain import native_live_controlled_close_schema_v1 as close
from scripts import current0058_full_hermetic_contract_v1 as full
from scripts import verify_final_hermetic as reports
from scripts import verify_windows_regression as windows
from tests.historical0055_metadata_v1 import FUTURE_TABLES, physical55_expected_metadata

ROOT = Path(__file__).resolve().parents[2]
MODULE = "tests.integration.test_native_live_controlled_close_v1_pg"
PG_PATH = MODULE.replace(".", "/") + ".py"


def _actual_source_cases(module=MODULE):
    """Read literal pytest identities without importing a PostgreSQL fixture."""
    tree = ast.parse(
        (ROOT / (module.replace(".", "/") + ".py")).read_text(encoding="utf8")
    )
    cases = []
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef) or not node.name.startswith(
            "test_"
        ):
            continue
        decorators = [
            item
            for item in node.decorator_list
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and item.func.attr == "parametrize"
        ]
        assert len(decorators) <= 1
        if not decorators:
            cases.append((module, node.name))
            continue
        decorator = decorators[0]
        assert len(decorator.args) == 2 and not decorator.keywords
        assert type(ast.literal_eval(decorator.args[0])) is str
        labels = ast.literal_eval(decorator.args[1])
        assert type(labels) in (tuple, list) and all(
            type(label) is str for label in labels
        )
        cases.extend((module, node.name + "[" + label + "]") for label in labels)
    return tuple(cases)


@pytest.mark.parametrize(
    "module,roster,count",
    (
        (
            "tests.integration.test_native_live_physical_v1_pg",
            full.PHYSICAL_LIVE_CASES,
            3,
        ),
        (
            "tests.integration.test_native_live_lifecycle_v1_pg",
            full.LIVE_LIFECYCLE_CASES,
            16,
        ),
    ),
)
def test_all_physical_and_restart_source_cases_are_mandatory_once(
    module, roster, count
):
    actual = _actual_source_cases(module)
    assert len(actual) == len(set(actual)) == count
    assert actual == roster
    for case in actual:
        assert full.REQUIRED_CASES.count(case) == full.SUCCESSOR_CASES.count(case) == 1
        node = case[0].replace(".", "/") + ".py::" + case[1]
        assert [
            index
            for index in range(5)
            if full.collection.selected_for("postgres", index, node)
        ] == [2]


def test_all43_real_source_case_identities_are_mandatory_in_both_closed_rosters():
    actual = _actual_source_cases()
    assert len(actual) == len(set(actual)) == 43
    assert actual == full.LIVE_CONTROLLED_CLOSE_CASES
    for case in actual:
        assert full.REQUIRED_CASES.count(case) == full.SUCCESSOR_CASES.count(case) == 1
        node = case[0].replace(".", "/") + ".py::" + case[1]
        assert full.collection.selected_for("full", None, node)
        assert [
            index
            for index in range(5)
            if full.collection.selected_for("postgres", index, node)
        ] == [2]
        assert not any(
            full.collection.selected_for("shard", index, node) for index in range(8)
        )
    assert full.collection.POSTGRES_COMPONENT_COUNT == 5
    assert len(full.STOPPED_SETUP_CASES) == 2


def test_close_pg_is_explicitly_classified_once_and_never_as_windows_non_db():
    assert PG_PATH in full.collection.SUCCESSOR_PATHS
    assert windows.POSTGRES_INTEGRATION.count(Path(PG_PATH).name) == 1
    assert Path(PG_PATH).name not in windows.NO_DB_INTEGRATION


def test_both_registered_models_are_current_only_and_historical_expected_model_keeps_real_drift():
    registered = (
        models.NativeLiveControlledCloseIntentV1,
        models.NativeLiveControlledCloseClaimV1,
    )
    assert tuple(model.__tablename__ for model in registered) == close.TABLES
    for model in registered:
        assert models.__all__.count(model.__name__) == 1
        assert Base.metadata.tables[model.__tablename__] is model.__table__
        assert model.__tablename__ in FUTURE_TABLES
    before = tuple(Base.metadata.tables)
    expected = physical55_expected_metadata(Base.metadata)
    assert not set(close.TABLES).intersection(expected.tables)
    assert tuple(Base.metadata.tables) == before
    # Filtering applies only to this explicitly built expected model, never
    # to actual reflection. Unknown names must still report real schema drift.
    assert set(expected.tables) == set(Base.metadata.tables) - FUTURE_TABLES


def _junit(tmp_path, cases):
    root = Element("testsuites")
    suite = SubElement(
        root, "testsuite", tests=str(len(cases)), failures="0", errors="0", skipped="0"
    )
    for module, name in cases:
        SubElement(suite, "testcase", classname=module, name=name)
    path = tmp_path / "close34.xml"
    path.write_bytes(tostring(root))
    return path


@pytest.mark.parametrize(
    "omitted", full.LIVE_CONTROLLED_CLOSE_CASES, ids=lambda item: item[1]
)
def test_any_one_missing_close_parameter_is_an_incomplete_component(tmp_path, omitted):
    selected = [case for case in full.LIVE_CONTROLLED_CLOSE_CASES if case != omitted]
    with pytest.raises(ValueError):
        reports.verify_pytest_report(
            _junit(tmp_path, selected),
            required_cases=full.LIVE_CONTROLLED_CLOSE_CASES,
            allow_unrelated_skips=False,
        )


def test_full43_synthetic_report_has_no_authority_and_cannot_borrow_a_duplicate_case(
    tmp_path,
):
    result = reports.verify_pytest_report(
        _junit(tmp_path, full.LIVE_CONTROLLED_CLOSE_CASES),
        required_cases=full.LIVE_CONTROLLED_CLOSE_CASES,
        allow_unrelated_skips=False,
    )
    assert result["tests"] == 43 and len(result["required_case_passed"]) == 43
    duplicated = (
        *full.LIVE_CONTROLLED_CLOSE_CASES,
        full.LIVE_CONTROLLED_CLOSE_CASES[0],
    )
    with pytest.raises(ValueError):
        reports.verify_pytest_report(
            _junit(tmp_path, duplicated),
            required_cases=full.LIVE_CONTROLLED_CLOSE_CASES,
            allow_unrelated_skips=False,
        )
