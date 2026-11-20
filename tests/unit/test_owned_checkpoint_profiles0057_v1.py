"""Closed profile DATA dispatch; synthetic captures never grant a source cap."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.native_role_witness_0056_v1 import RestrictedNativeRoles0056WitnessV1
from app.database.repositories import qualification_native_checkpoint_v2 as checkpoint
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)
from app.domain import native_database_profile_v1 as profiles
from app.trade_qualification import controlled_current_mirror_current55_v1 as mirror
from app.trade_qualification import controlled_writer_coverage_current55_v2 as coverage
from app.trade_qualification.demo_control import ControlScope, stopped_genesis


def roles(profile):
    observed = checkpoint.locked.roles56.RestrictedNativeRoles0056ObservationV1(
        "1" * 64,
        "2" * 64,
        "3" * 64,
        "4" * 64,
        schema=profiles.schema(
            profile, "ctcc.native_only.restricted0056_role_observation.v1"
        ),
        database_head=profiles.head(profile),
    )
    return RestrictedNativeRoles0056WitnessV1(
        observed,
        "5" * 64,
        "6" * 64,
        schema=profiles.schema(
            profile, "ctcc.native_only.restricted0056_runtime_witness.v1"
        ),
    )


@pytest.mark.parametrize("profile", [profiles.DB0056, profiles.DB0057])
def test_original_typed_role_profile_routes_without_promoting_deny(profile):
    original = roles(profile)
    assert checkpoint._profile_for_original_roles_v1(original) is profile
    assert original.execution_authority is original.startup_admission is False
    assert original.observation.admission == "DENY"


@pytest.mark.parametrize(
    "change",
    ["head", "schema", "witness_schema", "observation", "foreign", "admission"],
)
def test_partial_unknown_or_mixed_role_profile_cannot_select_a_contract(change):
    original = roles(profiles.DB0057)
    if change == "head":
        original = replace(
            original, observation=replace(original.observation, database_head="0058")
        )
    elif change == "schema":
        original = replace(
            original,
            observation=replace(
                original.observation, schema=roles(profiles.DB0056).observation.schema
            ),
        )
    elif change == "witness_schema":
        original = replace(original, schema=roles(profiles.DB0056).schema)
    elif change == "observation":
        original = replace(original, observation=SimpleNamespace(database_head="0057"))
    elif change == "foreign":
        original = {"database_head": "0057"}
    else:
        object.__setattr__(original.observation, "admission", "PASS")
    with pytest.raises(checkpoint.ledger.QualificationLedgerError):
        checkpoint._profile_for_original_roles_v1(original)


@pytest.mark.parametrize("profile", [profiles.DB0056, profiles.DB0057])
async def test_held_checkpoint_preserves_default56_call_shape_and_explicit57_rechecks(
    monkeypatch, profile
):
    engine = object()
    selected = ConfiguredNativeDemoFactoriesV2(
        SimpleNamespace(kw={"bind": SimpleNamespace(sync_engine=engine)}),
        *(object() for _ in range(4)),
    )
    session = SimpleNamespace(get_bind=Mock(return_value=engine), execute=AsyncMock())
    catalog = SimpleNamespace(database_head=profiles.head(profile))
    control = stopped_genesis(ControlScope("demo", "730061"), datetime.now(UTC))
    observation = SimpleNamespace(
        database_head=profiles.head(profile),
        control=SimpleNamespace(state=control, event_sha256="a" * 64),
        writer_catalog=catalog,
    )
    original_roles = roles(profile)
    seen = []

    @asynccontextmanager
    async def lower(actual, actual_session, scope, **kwargs):
        assert (actual, actual_session, scope) == (selected, session, control.scope)
        assert kwargs.pop("roles") is original_roles
        assert (
            kwargs.pop("purpose")
            is checkpoint.locked.NativeWriterControlPurpose0056.PREPARE_OBSERVATION
        )
        assert kwargs == profiles.arguments(profile)
        yield SimpleNamespace(
            observation=observation, check_held=lambda: seen.append("held")
        )

    repeat = AsyncMock(return_value=catalog)
    monkeypatch.setattr(checkpoint.locked, "held_native_writer_control_0056_v1", lower)
    monkeypatch.setattr(
        checkpoint.locked.writer56,
        "observe_restricted_readwrite_writer_0056_v1",
        repeat,
    )
    async with checkpoint.held_qualification_checkpoint_0056_v1(
        selected, session, control.scope, roles=original_roles
    ) as held:
        assert held.profile is profile and held.control is observation
        assert checkpoint._ACTIVE_HELD_CHECKPOINTS_0056[id(held)] is held
    assert id(held) not in checkpoint._ACTIVE_HELD_CHECKPOINTS_0056
    assert repeat.await_count == 2
    for call in repeat.await_args_list:
        assert call.args == (session,) and call.kwargs == profiles.arguments(profile)


def _source(profile):
    selected = ConfiguredNativeDemoFactoriesV2(*(object() for _ in range(5)))
    return SimpleNamespace(
        db0056_factories=selected,
        db0056_roles=roles(profile),
        session_factory=selected.writer_factory,
    )


def test_coverage57_receipt_labels_actual_successor_while56_contract_stays_exact():
    assert coverage._coverage_receipt_context_v2(_source(profiles.DB0056)) == {
        "schema_version": "ctcc.controlled_source_coverage.current55.v2"
    }
    assert coverage._coverage_receipt_context_v2(_source(profiles.DB0057)) == {
        "schema_version": "ctcc.controlled_source_coverage.current57.v2",
        "database_head": "0057",
    }
    legacy = SimpleNamespace(db0056_factories=None, db0056_roles=None)
    assert coverage._coverage_receipt_context_v2(legacy) == {
        "schema_version": "ctcc.controlled_source_coverage.current55.v2"
    }


def test_coverage_and_mirror_reject_mixed_schema_without_falling_back_to55():
    source = _source(profiles.DB0057)
    assert coverage._database_profile(source) == mirror._mirror_database_profile(source)
    source.db0056_roles = replace(
        source.db0056_roles, schema=roles(profiles.DB0056).schema
    )
    with pytest.raises(coverage.ControlledCoverageSourceErrorV2):
        coverage._database_profile(source)
    with pytest.raises(mirror.ControlledCurrentMirrorErrorV1):
        mirror._mirror_database_profile(source)
