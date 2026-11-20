"""Full report equality and explicit old-model projection; no SQL or venue."""

from copy import deepcopy
from xml.etree.ElementTree import Element, SubElement, tostring

import pytest
from sqlalchemy import Column, Index, Integer, MetaData, Table

from scripts import historical0056_on_current0058_observation_v1 as subject
from scripts import verify_final_hermetic as shared
from tests.historical0055_metadata_v1 import physical55_expected_metadata


def _report(tmp_path, *, omit=None):
    names = ("component_a", "component_b", "historical_a", "historical_b")
    root = Element("testsuites")
    suite = SubElement(
        root,
        "testsuite",
        tests=str(len(names) - (omit is not None)),
        failures="0",
        errors="0",
        skipped="0",
    )
    for name in names:
        if name != omit:
            SubElement(suite, "testcase", classname="synthetic.report", name=name)
    path = tmp_path / "report.xml"
    path.write_bytes(tostring(root))
    return path


def _check(path, expected):
    return subject._verified_component_tests(
        path,
        expected,
        component_cases=(
            ("synthetic.report", "component_a"),
            ("synthetic.report", "component_b"),
        ),
        historical_cases=(
            ("synthetic.report", "historical_a"),
            ("synthetic.report", "historical_b"),
        ),
        allow_unrelated_skips=False,
    )


def _expected(path):
    return shared.verify_pytest_report(
        path,
        required_cases=(
            ("synthetic.report", "component_a"),
            ("synthetic.report", "component_b"),
        ),
    )


def test_full_component_summary_keeps_its_own_required_set_while_all_history_is_replayed(
    tmp_path,
):
    path = _report(tmp_path)
    expected = _expected(path)
    assert _check(path, expected) == expected
    assert len(expected["required_case_passed"]) == 2


@pytest.mark.parametrize("omitted", ("component_a", "historical_a"))
def test_missing_required_component_or_historical_case_is_rejected(tmp_path, omitted):
    path = _report(tmp_path, omit=omitted)
    with pytest.raises(ValueError):
        _check(path, _expected(path))


@pytest.mark.parametrize("field", ("sha256", "tests", "required_case_passed"))
def test_changed_component_readback_is_rejected(tmp_path, field):
    path = _report(tmp_path)
    expected = deepcopy(_expected(path))
    expected[field] = "changed"
    with pytest.raises(ValueError):
        _check(path, expected)


def test_old_expected_model_removes_only_declared_later_objects_and_does_not_mutate_current():
    source = MetaData()
    old = Table(
        "qualification_submission_outcomes",
        source,
        Column("id", Integer, primary_key=True),
    )
    original = Index("original55_index", old.c.id)
    future = Index("qualification_native_protected_hold_once_v58", old.c.id)
    Table("native_live_controls_v1", source, Column("id", Integer, primary_key=True))
    Table("unreviewed_extra_table", source, Column("id", Integer, primary_key=True))
    target = physical55_expected_metadata(source)
    assert set(target.tables) == {
        "qualification_submission_outcomes",
        "unreviewed_extra_table",
    }
    assert {i.name for i in target.tables[old.name].indexes} == {"original55_index"}
    assert source.tables[old.name].indexes == {original, future}
    # No include_object/include_name/reflection filter exists: a real DB55
    # containing any extra table/index will still produce removal drift.
