"""DB0056 V6 route and catalog guards with synthetic database observations."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.database.repositories.demo_control import ControlObservation
from app.domain.source_primitives import canonical
from app.trade_qualification import native_controlled_account_source_v6 as source

_AT = datetime(2026, 11, 15, tzinfo=UTC)
_SCOPE = source.control.ControlScope("demo", "085002")
_NAMES = "writer", "custody", "frame"
_ROUTE = "postgresql+asyncpg", "127.0.0.1", 5432, "ctcc"
_PG = (212, _AT, 170011, "127.0.0.1", 5432)
_ACTOR = (*(_NAMES[0],) * 2, "none", 21, *_PG)
_RAW = (*(_NAMES[2],) * 2, "none", 24, *_PG)
_PINS = source.control.ControlPins("1" * 64, "2" * 64, "3" * 64)
_EVENT = "4" * 64


@pytest.fixture
def selected(monkeypatch):
    writer, custody, frame = object(), object(), object()
    factories = source.selector56.ConfiguredNativeDemoFactoriesV2(
        writer, custody, frame, object(), object()
    )
    report = source.control56.roles56.RestrictedNativeRoles0056ObservationV1(
        "4" * 64,
        "5" * 64,
        "6" * 64,
        "7" * 64,
    )
    roles = source.witness56.RestrictedNativeRoles0056WitnessV1(
        report,
        *source.witness56.runtime_witness_0056(_ACTOR, _NAMES, (_ROUTE,) * 3),
    )
    selection = object()
    record = SimpleNamespace(
        raw_reader_factory=frame,
        read_session=object(),
        private_client=object(),
        current_plan=object(),
    )
    repository = source.repository56.DemoControlRepository0056V1(
        factories, roles=roles, scope=_SCOPE, clock=lambda: _AT
    )
    state = {
        "control": {
            "repository": repository,
            "db0056_roles": roles,
            "db0056_factories": factories,
            "identity_selection": selection,
            "identity_record": record,
            "factory": writer,
            "scope": _SCOPE,
            "custody_original_factory": custody,
            "read_session": record.read_session,
            "private_client": record.private_client,
            "plan": record.current_plan,
        }
    }

    def replay(value):
        assert value is selection
        return record, factories, roles

    monkeypatch.setattr(source.selector56, "_controlled_identity_0056", replay)
    return SimpleNamespace(state=state, factories=factories, roles=roles)


def test_v6_branch_requires_exact_repository_and_private_selected_record(selected):
    assert source._owned_database_0056_v6(selected.state) == (
        selected.factories,
        selected.roles,
    )
    selected.state["control"]["plan"] = object()
    with pytest.raises(
        source.ControlledAccountSourceError, match="original_0056_owner_changed"
    ):
        source._owned_database_0056_v6(selected.state)
    selected.state["control"].pop("db0056_roles")
    with pytest.raises(
        source.ControlledAccountSourceError, match="original_0056_owner_required"
    ):
        source._owned_database_0056_v6(selected.state)


def test_v6_branch_keeps_explicit_55_repository_and_refuses_unknown():
    old = object.__new__(source.DemoControlRepositoryV2)
    assert source._owned_database_0056_v6({"control": {"repository": old}}) is None
    with pytest.raises(
        source.ControlledAccountSourceError, match="original_0056_owner_required"
    ):
        source._owned_database_0056_v6({"control": {"repository": object()}})


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", (None, "event", "expiry", "disarm", "owner"))
async def test_db0056_writer_holds_exact_live_control_before_current_source(
    monkeypatch, mutation
):
    state = source.control.acquire(
        None, scope=_SCOPE, owner_sha256="a" * 64, pins=_PINS, now=_AT
    )
    state = source.control.advance(
        state,
        owner_sha256=state.owner_sha256,
        owner_epoch=1,
        expected_revision=1,
        action="arm_requested",
        command_id="b" * 64,
        now=_AT,
        arm_expires_at=_AT + timedelta(seconds=10),
    )
    expected = ControlObservation(state, _EVENT, _AT)
    expiry = _AT + timedelta(seconds=8)
    actual = ControlObservation(state, _EVENT, _AT + timedelta(seconds=1))
    if mutation == "event":
        actual = replace(actual, event_sha256="e" * 64)
    elif mutation == "expiry":
        actual = replace(actual, observed_at=expiry)
    elif mutation == "disarm":
        actual = replace(
            actual, state=replace(state, arm_request_id=None, arm_expires_at=None)
        )
        expected = actual
    if mutation == "owner":
        owner = "f" * 64
    else:
        owner = state.owner_sha256
    factories, roles = object(), object()
    check = Mock()

    @asynccontextmanager
    async def begin():
        yield

    session = SimpleNamespace(begin=begin)

    @asynccontextmanager
    async def factory():
        yield session

    @asynccontextmanager
    async def held(selected, actual_session, scope, *, roles, purpose):
        assert selected is factories and actual_session is session and scope == _SCOPE
        assert roles is expected_roles
        assert (
            purpose
            is source.control56.NativeWriterControlPurpose0056.PREPARE_OBSERVATION
        )
        yield SimpleNamespace(
            observation=SimpleNamespace(control=actual), check_held=check
        )

    expected_roles = roles
    monkeypatch.setattr(source, "_check_v6", Mock())
    monkeypatch.setattr(source.control56, "held_native_writer_control_0056_v1", held)
    attempt = {
        "control": {
            "factory": factory,
            "scope": _SCOPE,
            "observation": expected,
            "owner_sha256": owner,
            "pins": _PINS,
        },
        "expires_at": expiry,
    }
    if mutation is None:
        await source._admit_writer_0056_v6(attempt, factories, roles)
    else:
        with pytest.raises(
            source.ControlledAccountSourceError, match="original_0056_Control_changed"
        ):
            await source._admit_writer_0056_v6(attempt, factories, roles)
    check.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", (None, "acl", "writer_oid", "reader_oid", "session_drift", "clone")
)
async def test_db0056_frame_proof_matches_selected_role_and_server(
    selected, monkeypatch, mutation
):
    state, factories, roles = selected.state, selected.factories, selected.roles
    reader = SimpleNamespace(kw={"bind": SimpleNamespace(url=object())})
    shared_pin = (object(), False, False)
    original_pin = (factories.frame_reader_factory, *shared_pin)
    state["factories"] = ((), (factories.frame_reader_factory, original_pin))
    state["reader_factory"] = reader
    document = {
        "schema": "ctcc.native_only.held_frame_catalog_0056.v1",
        "database_head": "0056",
        "current0056": {
            "profile": {
                "native_definitions_sha256": roles.observation.inherited_native_definitions_sha256
            },
            "raw": {"role": {"oid": _RAW[3]}},
            "writer": {"role": {"name": _NAMES[0], "oid": _ACTOR[3]}},
        },
        "granted_inherited_acl_shape_sha256": roles.observation.granted_inherited_acl_shape_sha256,
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    second = _RAW
    if mutation == "acl":
        document["granted_inherited_acl_shape_sha256"] = "a" * 64
    elif mutation in {"writer_oid", "reader_oid"}:
        document["current0056"]["writer" if mutation == "writer_oid" else "raw"][
            "role"
        ]["oid"] = 72
    elif mutation == "session_drift":
        second = (*_RAW[:3], 99, *_RAW[4:])
    elif mutation == "clone":
        shared_pin = (object(), False, False)
    monkeypatch.setattr(
        source.frame56, "_checked_factories", Mock(return_value=((), _NAMES))
    )
    monkeypatch.setattr(
        source.history,
        "_factory_pin",
        lambda factory: (
            original_pin
            if factory is factories.frame_reader_factory
            else (reader, *shared_pin)
        ),
    )
    monkeypatch.setattr(source.selector56, "_database_route", lambda _url: _ROUTE)
    monkeypatch.setattr(
        source.frame56, "_identity", AsyncMock(side_effect=[_RAW, second])
    )
    verify = AsyncMock(return_value=canonical(document))
    monkeypatch.setattr(source.frame56, "verify_held_frame_catalog_0056_v1", verify)
    session, fence = object(), SimpleNamespace(check=Mock())
    if mutation is None:
        assert (
            await source._reader_role_0056_v6(state, session, fence, factories, roles)
            == _RAW[3]
        )
        verify.assert_awaited_once_with(
            session, fence, factories.writer_factory, factories.custody_factory, reader
        )
    else:
        with pytest.raises(
            source.ControlledAccountSourceError, match="original_0056_frame"
        ):
            await source._reader_role_0056_v6(state, session, fence, factories, roles)
