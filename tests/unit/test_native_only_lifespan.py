"""No-database tests for the isolated native-only startup draft."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE = (
    Path(__file__).resolve().parents[2] / "app" / "core" / "native_only_lifespan.py"
)
SPEC = importlib.util.spec_from_file_location("native_only_lifespan_draft", MODULE)
assert SPEC is not None and SPEC.loader is not None
native = importlib.util.module_from_spec(SPEC)
import sys

sys.modules[SPEC.name] = native
SPEC.loader.exec_module(native)


def _settings(**overrides):
    values = {name: False for name in native._MUST_BE_FALSE}
    values.update(
        environment="production",
        trading_mode="okx_demo",
        web_concurrency=1,
        okx_demo_enabled=True,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _control(**overrides):
    row = {
        "control_revision": 7,
        "armed": False,
        "emergency_stop": False,
        "locked": False,
        "restart_latch_required": False,
        "lock_reasons": [],
        "active_trades": {},
        "symbol_cooldowns": {},
        "realized_pnl_events": [],
    }
    return row | overrides


class _Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def one_or_none(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, control, running):
        self.control, self.running = control, running
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def begin(self):
        return self

    async def execute(self, statement):
        sql = str(statement)
        self.queries.append(sql)
        if sql.startswith("SET TRANSACTION"):
            return _Result(None)
        if "demo_automation_state" in sql:
            return _Result(self.control)
        if "demo_soak_sessions" in sql:
            return _Result(self.running)
        raise AssertionError("unexpected source query")


class _Factory:
    def __init__(self, control, running=None):
        self.session = _Session(control, running)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.session


class _Automation:
    def __init__(self, after, *, status=None):
        self.repository = SimpleNamespace(load_state=self._load_state)
        self.after = after
        self.observed_status = status or SimpleNamespace(
            recovered=True, armed=False, emergency_stop=False
        )
        self.recover_calls = []

    async def _load_state(self):
        return self.after

    async def recover(self, *, require_existing=False):
        self.recover_calls.append(require_existing)

    async def status(self):
        return self.observed_status


@pytest.mark.parametrize("flag", native._MUST_BE_FALSE)
@pytest.mark.asyncio
async def test_incompatible_flag_denied_before_database_or_recovery(flag):
    factory = _Factory(_control())
    automation = _Automation(None)
    with pytest.raises(native.NativeOnlyLifespanError, match="configuration_denied"):
        await native.start_native_only(_settings(**{flag: True}), factory, automation)
    assert factory.calls == 0
    assert automation.recover_calls == []


@pytest.mark.asyncio
async def test_missing_control_never_calls_recovery_default_insert_path():
    factory = _Factory(None)
    automation = _Automation(None)
    with pytest.raises(native.NativeOnlyLifespanError, match="control_state_missing"):
        await native.start_native_only(_settings(), factory, automation)
    assert automation.recover_calls == []
    assert factory.session.queries[0] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    )


@pytest.mark.asyncio
async def test_running_soak_denied_without_mutating_observability():
    factory = _Factory(_control(), running=1)
    automation = _Automation(None)
    with pytest.raises(native.NativeOnlyLifespanError, match="running_soak_unresolved"):
        await native.start_native_only(_settings(), factory, automation)
    assert automation.recover_calls == []
    assert len(factory.session.queries) == 3
    assert all("UPDATE" not in query for query in factory.session.queries)


@pytest.mark.parametrize(
    "change",
    [
        {"control_revision": 0},
        {"armed": True, "restart_latch_required": False},
        {"emergency_stop": True, "locked": False},
        {"lock_reasons": None},
        {"active_trades": []},
        {"symbol_cooldowns": None},
        {"realized_pnl_events": None},
    ],
)
def test_unverified_control_state_denied(change):
    with pytest.raises(native.NativeOnlyLifespanError, match="control_state_invalid"):
        native._checked_control(_control(**change), None)


@pytest.mark.asyncio
async def test_real_recovery_requested_and_separate_readback_confirmed():
    before = _control(armed=True, restart_latch_required=True)
    after = {
        "_control_revision": 8,
        "armed": False,
        "emergency_stop": True,
        "locked": True,
        "_restart_latch_required": True,
    }
    factory = _Factory(before)
    automation = _Automation(
        after,
        status=SimpleNamespace(recovered=True, armed=False, emergency_stop=True),
    )
    await native.start_native_only(_settings(), factory, automation)
    assert automation.recover_calls == [True]


@pytest.mark.asyncio
async def test_ambiguous_recovery_readback_denied():
    factory = _Factory(_control())
    automation = _Automation(
        {"_control_revision": 9, "armed": False, "_restart_latch_required": False}
    )
    with pytest.raises(native.NativeOnlyLifespanError, match="readback_unconfirmed"):
        await native.start_native_only(_settings(), factory, automation)
    assert automation.recover_calls == [True]


def test_existing_estop_requires_persisted_restart_latch_after_recovery():
    before = native.ExistingControl(7, False, True, True, False)
    after = {
        "_control_revision": 8,
        "armed": False,
        "emergency_stop": True,
        "locked": True,
        "_restart_latch_required": False,
    }
    status = SimpleNamespace(recovered=True, armed=False, emergency_stop=True)
    with pytest.raises(native.NativeOnlyLifespanError, match="readback_unconfirmed"):
        native._check_recovery(before, after, status)
