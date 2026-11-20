"""Negative-only physical close DATA tests; no source or submit cap is minted."""

import asyncio
import builtins
from contextlib import asynccontextmanager
from types import FunctionType, SimpleNamespace

import pytest

from app.database.repositories import native_postsubmit_v1 as repository
from app.trade_qualification import native_controlled_close_v1 as close
from app.trade_qualification.reservations import LedgerScope


def _shared(*, stop=False, locked=False, reasons=None):
    return ((1, 7, False, stop, locked, True, [] if reasons is None else reasons),)


@pytest.mark.parametrize(
    "rows",
    [
        _shared(),
        _shared(stop=True, locked=True, reasons=["emergency_stop_engaged"]),
        _shared(locked=True, reasons=["drawdown_breach"]),
    ],
)
def test_typed_disarm_stop_and_loss_latch_do_not_grant_or_block_exact_negative_close(
    rows,
):
    observed = close._negative_shared_control_v1(rows)
    assert observed[2] is False
    assert observed[-1] == tuple(rows[0][-1])


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "duplicate",
        "foreign",
        "armed",
        "revision",
        "stop_type",
        "latch_type",
        "reason_type",
        "duplicate_reason",
        "unlocked_stop",
        "empty_lock",
    ],
)
def test_unknown_shared_control_never_passes_the_negative_fence(change):
    rows = _shared()
    row = list(rows[0])
    if change == "missing":
        rows = ()
    elif change == "duplicate":
        rows = (*rows, *rows)
    else:
        if change == "foreign":
            row[0] = 2
        elif change == "armed":
            row[2] = True
        elif change == "revision":
            row[1] = True
        elif change == "stop_type":
            row[3] = 0
        elif change == "latch_type":
            row[5] = 1
        elif change == "reason_type":
            row[6] = [None]
        elif change == "duplicate_reason":
            row[6] = ["x", "x"]
        elif change == "unlocked_stop":
            row[3] = True
        else:
            row[4] = True
        rows = (tuple(row),)
    with pytest.raises(
        close.NativeControlledCloseError, match="shared_control_unconfirmed$"
    ):
        close._negative_shared_control_v1(rows)


@pytest.mark.parametrize("failure", [None, "post", "catalog", "control"])
async def test_original_external_close_context_keeps_db0056_held_through_post_and_exit(
    failure,
):
    # Exact context body, inert captures. No genuine module registry or guard
    # is patched, and the cloned code cannot satisfy the native source guard.
    events, active = [], {}
    factory, binding, key = object(), object(), object()
    source = SimpleNamespace(
        session_factory=factory,
        scope=LedgerScope(
            environment="demo", account_id="730060", settlement_currency="USDT"
        ),
    )
    view = SimpleNamespace(binding=binding)
    selected = SimpleNamespace(writer_factory=factory)
    original_control = SimpleNamespace(emergency_stop=True, arm_request_id=None)

    class Session:
        async def execute(self, statement):
            events.append("setting")

    session = Session()

    async def recheck_catalog():
        events.append("catalog")
        if failure == "catalog":
            raise ValueError("synthetic_catalog_drift")

    async def recheck_control():
        events.append("control")
        return (object() if failure == "control" else original_control), "a" * 64

    held = SimpleNamespace(
        selected=selected,
        recheck_catalog=recheck_catalog,
        recheck_control=recheck_control,
        control=SimpleNamespace(
            control=SimpleNamespace(state=original_control, event_sha256="a" * 64)
        ),
    )

    @asynccontextmanager
    async def checkpoint(*args, **kwargs):
        events.append("database_enter")
        try:
            yield held
        finally:
            events.append("database_exit")

    raw = repository._held_owned_close_database_v1.__wrapped__
    namespace = dict(raw.__globals__)
    namespace.update(
        _close_source=lambda *args, **kwargs: source,
        _database_profile=lambda actual: (selected, object()),
        _HELD_POSTSUBMIT_0056=active,
        checkpoint=SimpleNamespace(held_qualification_checkpoint_0056_v1=checkpoint),
    )
    cloned = FunctionType(
        raw.__code__, namespace, raw.__name__, raw.__defaults__, raw.__closure__
    )

    async def run():
        async with asynccontextmanager(cloned)(view, session=session, key=key):
            assert active[session] == (binding, held)
            events.append("post")
            await asyncio.sleep(0)
            assert active[session] == (binding, held)
            if failure == "post":
                raise ValueError("synthetic_post_unknown")
        assert (
            original_control.emergency_stop and original_control.arm_request_id is None
        )

    if failure is None:
        await run()
    else:
        with pytest.raises(ValueError):
            await run()
    assert active == {}
    assert (
        events.index("database_enter")
        < events.index("post")
        < events.index("database_exit")
    )


@pytest.mark.parametrize("current56", [False, True])
async def test_terminal_spool_uses_original_catalog_path_and_unchanged_row_policy(
    current56,
):
    events, binding, session, spool, owner = [], object(), object(), object(), object()

    async def historical(*args):
        events.append("strict55")
        return "old"

    async def inherited(*args):
        events.append("inherited_rows")
        return "new"

    async def held(*args):
        events.append("original56_held")

    reports = SimpleNamespace(
        verify_native_terminal_spool_v55=historical,
        _replay_native_terminal_spool=inherited,
    )
    original_import = builtins.__import__

    def import_inert(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "app.database.repositories" and fromlist == (
            "native_closed_reporting_v55",
        ):
            return SimpleNamespace(native_closed_reporting_v55=reports)
        return original_import(name, globals, locals, fromlist, level)

    namespace = dict(repository._verify_owned_terminal_spool_v1.__globals__)
    namespace.update(
        __builtins__={**vars(builtins), "__import__": import_inert},
        _database_profile=lambda actual: object() if current56 else None,
        _checked_postsubmit_catalog=held,
        _source=lambda actual: events.append("source"),
    )
    cloned = FunctionType(
        repository._verify_owned_terminal_spool_v1.__code__, namespace
    )
    result = await cloned(binding, owner, session, spool)
    assert result == ("new" if current56 else "old")
    assert events == (
        ["original56_held", "inherited_rows", "source"]
        if current56
        else ["strict55", "source"]
    )


def test_external_session_or_view_data_cannot_register_a_negative_close():
    with pytest.raises(repository.NativePostsubmitRepositoryError):
        repository._close_source(
            SimpleNamespace(), session=object(), key=close._DB_KEY_V1
        )
