"""Offline checks for the inert DB0056 policy-verified custody projection."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.core import native_role_witness_0056_v1 as witness56
from app.core import native_static_custody_0056_v2 as subject
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)

_ROUTE = ("postgresql+asyncpg", "db", 5432, "ctcc")
_PORTABLE = ("1" * 64, "2" * 64)
_DYNAMIC = "3" * 64


def _observed():
    digest = subject._static_identity_sha256(
        93, "ctcc", "custody", 211, 101, _ROUTE, *_PORTABLE
    )
    return subject.NativeStaticCustody0056V2(
        93,
        "ctcc",
        "custody",
        211,
        101,
        _ROUTE,
        *_PORTABLE,
        digest,
        _DYNAMIC,
        "4" * 64,
        "5" * 64,
    )


def _expected(observed):
    return {
        "expected_database_oid": observed.database_oid,
        "expected_database_name": observed.database_name,
        "expected_custody_role_name": observed.custody_role_name,
        "expected_custody_role_oid": observed.custody_role_oid,
        "expected_table_owner_oid": observed.table_owner_oid,
        "expected_route": observed.route,
        "expected_inherited_native_definitions_sha256": (
            observed.inherited_native_definitions_sha256
        ),
        "expected_granted_inherited_acl_shape_sha256": (
            observed.granted_inherited_acl_shape_sha256
        ),
        "expected_static_identity_sha256": observed.static_identity_sha256,
    }


def test_static_digest_binds_typed_custody_fields_and_portable_graph():
    observed = _observed()
    assert observed.static_identity_sha256 != subject._static_identity_sha256(
        94, "ctcc", "custody", 211, 101, _ROUTE, *_PORTABLE
    )
    assert observed.static_identity_sha256 != subject._static_identity_sha256(
        93, "ctcc", "custody", 212, 101, _ROUTE, *_PORTABLE
    )
    assert observed.static_identity_sha256 != subject._static_identity_sha256(
        93, "ctcc", "custody", 211, 102, _ROUTE, *_PORTABLE
    )
    assert observed.static_identity_sha256 != subject._static_identity_sha256(
        93, "ctcc", "custody", 211, 101, _ROUTE, "9" * 64, _PORTABLE[1]
    )
    assert observed.static_identity_sha256 != subject._static_identity_sha256(
        93, "ctcc", "custody", 211, 101, _ROUTE, _PORTABLE[0], "9" * 64
    )
    # The current-boot full hash is deliberately separate from static identity.
    assert replace(
        observed, full_current_catalog_sha256="9" * 64
    ).static_identity_sha256 == (observed.static_identity_sha256)


def test_static_compare_is_inert_and_rejects_drift():
    observed = _observed()
    expected = _expected(observed)
    result = subject.compare_static_custody_0056_v2(observed, **expected)
    assert result["static_identity_sha256"] == observed.static_identity_sha256
    assert result["full_current_catalog_sha256"] == _DYNAMIC
    assert result["admission"] == "DENY"
    assert result["startup_admission"] is result["execution_authority"] is False
    assert result["deployment_reference_authenticated"] is False
    for altered in (
        {"expected_custody_role_oid": 212},
        {"expected_table_owner_oid": 102},
        {"expected_database_oid": 94},
        {"expected_route": ("postgresql+asyncpg", "other", 5432, "ctcc")},
        {"expected_granted_inherited_acl_shape_sha256": "9" * 64},
        {"expected_static_identity_sha256": "0" * 64},
    ):
        with pytest.raises(subject.NativeStaticCustody0056Error):
            subject.compare_static_custody_0056_v2(observed, **{**expected, **altered})
    for modified in (
        replace(observed, custody_role_oid=212),
        replace(observed, full_current_catalog_sha256="wrong"),
    ):
        with pytest.raises(subject.NativeStaticCustody0056Error):
            subject.compare_static_custody_0056_v2(modified, **expected)
    assert "ctcc" not in repr(observed)


@pytest.fixture
def scenario(monkeypatch):
    class Factory:
        def __init__(self, name):
            self.name = name
            self.kw = {"bind": SimpleNamespace(url=_ROUTE)}

    factories = tuple(Factory(name) for name in ("writer", "custody", "frame"))
    selected = ConfiguredNativeDemoFactoriesV2(
        *factories, factories[1].kw["bind"], factories[2].kw["bind"]
    )
    witness = witness56.RestrictedNativeRoles0056WitnessV1(
        roles56.RestrictedNativeRoles0056ObservationV1(
            _PORTABLE[0], "6" * 64, _DYNAMIC, _PORTABLE[1]
        ),
        "4" * 64,
        "5" * 64,
    )
    state = SimpleNamespace(
        selected=selected,
        witnesses=[witness, witness],
        typed=(93, "ctcc", "custody", 211, 101, _DYNAMIC),
        events=[],
    )
    monkeypatch.setattr(
        roles56.native_factories,
        "_factory_route",
        lambda factory: (None, None, "shared", factory.name),
    )
    monkeypatch.setattr(subject.selectors, "_database_route", lambda _: _ROUTE)

    async def observe(selected, *, source):
        assert selected is state.selected and source == Path("synthetic")
        state.events.append("strict_witness")
        return state.witnesses.pop(0)

    async def typed(selected, name, route):
        assert selected is state.selected and name == "custody" and route == _ROUTE
        state.events.append("typed_read")
        return state.typed

    monkeypatch.setattr(
        subject.witness56, "observe_restricted_native_roles_0056_witness_v1", observe
    )
    monkeypatch.setattr(subject, "_read_typed_custody", typed)
    return state


@pytest.mark.asyncio
async def test_observer_requires_strict_fresh_catalog_before_and_after_typed_read(
    scenario,
):
    observed = await subject.observe_static_custody_0056_v2(
        scenario.selected, source=Path("synthetic")
    )
    assert scenario.events == ["strict_witness", "typed_read", "strict_witness"]
    assert observed.full_current_catalog_sha256 == _DYNAMIC
    assert observed.static_identity_sha256 == _observed().static_identity_sha256
    assert observed.admission == "DENY"
    assert observed.startup_admission is observed.execution_authority is False


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["full_hash", "second_witness"])
async def test_observer_denies_catalog_drift(scenario, fault):
    if fault == "full_hash":
        scenario.typed = (*scenario.typed[:5], "8" * 64)
    else:
        scenario.witnesses[1] = replace(
            scenario.witnesses[1],
            observation=replace(
                scenario.witnesses[1].observation,
                raw_custody_catalog_sha256="8" * 64,
            ),
        )
    with pytest.raises(subject.NativeStaticCustody0056Error):
        await subject.observe_static_custody_0056_v2(
            scenario.selected, source=Path("synthetic")
        )
    assert scenario.events == ["strict_witness", "typed_read", "strict_witness"]
