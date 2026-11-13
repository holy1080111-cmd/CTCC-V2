"""Exact DB0056 mirror routing and held checkpoint boundary tests."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.orm import Session

from app.database.repositories import qualification_native_checkpoint_v2 as checkpoint
from app.trade_qualification import controlled_current_mirror_current55_v1 as mirror
from app.trade_qualification import demo_control as control
from app.trade_qualification.reservations import LedgerScope

_SCOPE = control.ControlScope("demo", "700042")
_LEDGER = LedgerScope(account_id=_SCOPE.account_id, settlement_currency="USDT")
_AT = datetime(2026, 11, 14, tzinfo=UTC)
_EVENT = "a" * 64


@pytest.fixture
def setup(monkeypatch):
    selected = mirror.ConfiguredNativeDemoFactoriesV2(*(object() for _ in range(5)))
    roles = mirror.RestrictedNativeRoles0056WitnessV1(
        checkpoint.locked.roles56.RestrictedNativeRoles0056ObservationV1(
            "1" * 64, "2" * 64, "3" * 64, "4" * 64
        ),
        "5" * 64,
        "6" * 64,
    )
    actual = control.stopped_genesis(_SCOPE, _AT)
    expected = SimpleNamespace(
        db0056_factories=selected,
        db0056_roles=roles,
        session_factory=selected.writer_factory,
        control_state=actual,
        control_event_sha256=_EVENT,
        scope=_LEDGER,
    )
    control_observation = SimpleNamespace(state=actual, event_sha256=_EVENT)
    case = SimpleNamespace(
        state={"source": expected},
        session=object(),
        held=SimpleNamespace(control=SimpleNamespace(control=control_observation)),
        trace=[],
        repository=object(),
        row=object(),
        selected=selected,
        roles=roles,
    )

    async def setup_lock(state, session):
        assert (state, session) == (case.state, case.session)
        case.trace.append("capture_and_uid")
        return _SCOPE

    @asynccontextmanager
    async def held(factories, session, scope, *, roles):
        assert (factories, session, scope, roles) == (
            selected,
            case.session,
            _SCOPE,
            case.roles,
        )
        case.trace.append("held")
        try:
            yield case.held
        finally:
            case.trace.append("released")

    async def after_lock(state, session, actual, event):
        assert (state, session, actual, event) == (
            case.state,
            case.session,
            expected.control_state,
            _EVENT,
        )
        case.trace.append("scope_mirror")
        return case.repository, case.row

    monkeypatch.setattr(mirror, "_setup_and_lock", setup_lock)
    monkeypatch.setattr(mirror, "_check", Mock())
    case.patched_held = held
    monkeypatch.setattr(mirror, "_lock_scope_and_mirror", after_lock)
    old = AsyncMock(return_value=(actual, _EVENT))
    monkeypatch.setattr(
        mirror.controls55.DemoControlRepositoryV2, "read_in_transaction", old
    )
    case.old = old
    return case


async def test_mirror_preserves_legacy_before_uid_then_held_database_and_scope(
    setup, monkeypatch
):
    monkeypatch.setattr(
        checkpoint, "held_qualification_checkpoint_0056_v1", setup.patched_held
    )
    async with mirror._held_mirror_transaction(setup.state, setup.session) as result:
        assert result == (setup.repository, setup.row, setup.held)
        assert setup.trace == ["capture_and_uid", "held", "scope_mirror"]
        assert mirror._held_checkpoint_args(result[2]) == {"_held0056": setup.held}
    assert setup.trace[-1] == "released"
    setup.old.assert_not_awaited()


async def test_legacy_mirror_keeps_exact_original_guard(setup):
    setup.state["source"].db0056_factories = setup.state["source"].db0056_roles = None
    async with mirror._held_mirror_transaction(setup.state, setup.session) as result:
        assert result == (setup.repository, setup.row, None)
        assert mirror._held_checkpoint_args(result[2]) == {}
    setup.old.assert_awaited_once_with(setup.session, _SCOPE)


@pytest.mark.parametrize("foreign", ["roles", "factory", "writer"])
def test_mirror_does_not_fall_back_from_partial_db0056_profile(setup, foreign):
    source = setup.state["source"]
    setattr(
        source,
        {
            "roles": "db0056_roles",
            "factory": "db0056_factories",
            "writer": "session_factory",
        }[foreign],
        object(),
    )
    with pytest.raises(
        mirror.ControlledCurrentMirrorErrorV1, match="original_roles_required"
    ):
        mirror._mirror_database_profile(source)


async def test_checkpoint_guard_rejects_receipt_copy_detached_row_and_other_scope(
    setup, monkeypatch
):
    engine = object()
    selected = SimpleNamespace(
        writer_factory=SimpleNamespace(kw={"bind": SimpleNamespace(sync_engine=engine)})
    )
    session = SimpleNamespace(
        sync_session=Session(), get_bind=Mock(return_value=engine), execute=AsyncMock()
    )
    catalog = object()
    observation = SimpleNamespace(
        control=setup.held.control.control, writer_catalog=catalog
    )
    check = Mock()
    trace = []

    @asynccontextmanager
    async def bound(actual, actual_session, scope, *, roles, purpose):
        assert (actual, actual_session, scope, roles) == (
            selected,
            session,
            _SCOPE,
            setup.roles,
        )
        assert (
            purpose
            is checkpoint.locked.NativeWriterControlPurpose0056.PREPARE_OBSERVATION
        )
        yield SimpleNamespace(observation=observation, check_held=check)
        trace.append("closed")

    monkeypatch.setattr(checkpoint.locked, "held_native_writer_control_0056_v1", bound)
    repeat = AsyncMock(return_value=catalog)
    monkeypatch.setattr(
        checkpoint.locked.writer56,
        "observe_restricted_readwrite_writer_0056_v1",
        repeat,
    )
    repository = checkpoint.QualificationLedgerRepository(
        selected.writer_factory, clock=lambda: _AT
    )
    row = checkpoint.QualificationAccountScope(
        environment="demo", account_id=_SCOPE.account_id, settlement_currency="USDT"
    )
    verify = checkpoint.check_held_qualification_checkpoint_0056_v1
    async with checkpoint.held_qualification_checkpoint_0056_v1(
        selected, session, _SCOPE, roles=setup.roles
    ) as held:
        with pytest.raises(checkpoint.ledger.QualificationLedgerError):
            verify(repository, session, _LEDGER, row, held)
        session.sync_session.add(row)
        verify(repository, session, _LEDGER, row, held)
        copied = type(held)(held.selected, held.session, held.control, held._held)
        other = LedgerScope(account_id="700043", settlement_currency="USDT")
        for scope, proof in [(_LEDGER, copied), (other, held)]:
            with pytest.raises(checkpoint.ledger.QualificationLedgerError):
                verify(repository, session, scope, row, proof)
    with pytest.raises(checkpoint.ledger.QualificationLedgerError):
        verify(repository, session, _LEDGER, row, held)
    assert trace == ["closed"] and repeat.await_count == 2
