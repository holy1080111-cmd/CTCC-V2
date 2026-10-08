"""A stale automation process cannot retain another process's durable Arm."""

import asyncio
from copy import deepcopy

import pytest

from app.database.repositories.demo_automation import (
    DemoAutomationRepository,
    DemoAutomationStateConflict,
)
from tests.unit.test_demo_automation import (
    FakeDemo,
    MemoryAutomationRepository,
    make_service,
)


class SharedControlRepository(MemoryAutomationRepository):
    """Small CAS double so a stale run cannot overwrite an external stop."""

    async def save_state(self, state):
        expected = 1 if self.state is None else self.state["_control_revision"] + 1
        if state["_control_revision"] != expected:
            raise DemoAutomationStateConflict(
                "demo_automation_control_revision_conflict"
            )
        await super().save_state(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("external_change", ("stop", "revision", "missing"))
async def test_external_control_change_during_last_preflight_denies_submit(
    external_change,
):
    entered = asyncio.Event()
    release = asyncio.Event()

    class PausedBeforeSubmit(FakeDemo):
        async def place_order(self, request, *, before_submit=None):
            entered.set()
            await release.wait()
            return await super().place_order(request, before_submit=before_submit)

    demo = PausedBeforeSubmit()
    repository = SharedControlRepository()
    service = make_service(demo)
    service.repository = repository
    await service.recover()
    await service.arm()
    local_revision = service._control_revision
    run_task = asyncio.create_task(service.run_once(execute=True))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        if external_change == "stop":
            await repository.latch_emergency_stop("external_process_stop")
        elif external_change == "revision":
            changed = deepcopy(repository.state)
            changed["_control_revision"] += 1
            changed["last_error"] = "external_process_control_update"
            await repository.save_state(changed)
        else:
            repository.state = None
        assert service._control_revision == local_revision
        assert service._state["armed"] is True
        release.set()
        run = await asyncio.wait_for(run_task, timeout=2)
    finally:
        release.set()
        if not run_task.done():
            run_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await run_task

    assert demo.place_calls == []
    assert run.results[0].order_submission_attempted is False
    assert "demo_automation_shared_control_unconfirmed" in run.results[0].detail
    assert service._recovered is False
    assert service._state["emergency_stop"] is True
    assert service._state["armed"] is False
    if external_change == "stop":
        assert repository.state["emergency_stop"] is True
        assert "external_process_stop" in repository.state["lock_reasons"]
    elif external_change == "revision":
        assert repository.state["last_error"] == "external_process_control_update"


@pytest.mark.asyncio
async def test_final_control_read_failure_denies_without_claiming_submission():
    class ReadFailure(MemoryAutomationRepository):
        async def assert_current_execution_control(self, expected_revision):
            raise TimeoutError("synthetic DB read timeout")

    demo = FakeDemo()
    service = make_service(demo)
    service.repository = ReadFailure()
    await service.recover()
    await service.arm()

    run = await service.run_once(execute=True)

    assert demo.place_calls == []
    assert run.results[0].order_submission_attempted is False
    assert "demo_automation_shared_control_unconfirmed" in run.results[0].detail
    assert service._state["emergency_stop"] is True
    assert service._recovered is False


@pytest.mark.asyncio
async def test_final_control_read_requires_exact_boolean_and_revision_fields():
    repository = MemoryAutomationRepository()
    repository.state = {
        "armed": True,
        "emergency_stop": False,
        "locked": False,
        "_restart_latch_required": True,
        "_control_revision": 2,
    }
    assert await repository.assert_current_execution_control(2) == 2
    for field, value in (
        ("armed", 1),
        ("emergency_stop", None),
        ("locked", None),
        ("_restart_latch_required", None),
        ("_control_revision", True),
    ):
        changed = deepcopy(repository.state)
        changed[field] = value
        repository.state = changed
        with pytest.raises(DemoAutomationStateConflict):
            await repository.assert_current_execution_control(2)
        repository.state[field] = {
            "armed": True,
            "emergency_stop": False,
            "locked": False,
            "_restart_latch_required": True,
            "_control_revision": 2,
        }[field]


@pytest.mark.asyncio
async def test_repository_final_guard_reads_fresh_control_and_rejects_unknown_fields():
    class Result:
        def __init__(self, row):
            self.row = row

        def one_or_none(self):
            return self.row

    class Session:
        def __init__(self, factory):
            self.factory = factory

        async def __aenter__(self):
            return self

        async def __aexit__(self, *unused):
            return None

        async def execute(self, statement):
            self.factory.statements.append(statement)
            if self.factory.fail:
                raise TimeoutError("synthetic DB unavailable")
            return Result(self.factory.row)

    class Factory:
        row = (2, True, False, False, True)
        fail = False

        def __init__(self):
            self.statements = []

        def __call__(self):
            return Session(self)

    factory = Factory()
    repository = DemoAutomationRepository(factory)
    assert await repository.assert_current_execution_control(2) == 2
    for row in (
        None,
        (3, True, False, False, True),
        (2, 1, False, False, True),
        (2, True, None, False, True),
        (2, True, False, None, True),
        (2, True, False, False, None),
    ):
        factory.row = row
        with pytest.raises(DemoAutomationStateConflict):
            await repository.assert_current_execution_control(2)
    factory.fail = True
    with pytest.raises(TimeoutError):
        await repository.assert_current_execution_control(2)
    assert len(factory.statements) == 8


@pytest.mark.asyncio
async def test_local_disarm_during_database_guard_await_denies_stale_readback():
    entered = asyncio.Event()
    release = asyncio.Event()

    class DelayedGuard(SharedControlRepository):
        async def assert_current_execution_control(self, expected_revision):
            observed = await super().assert_current_execution_control(expected_revision)
            entered.set()
            await release.wait()
            return observed

    demo = FakeDemo()
    repository = DelayedGuard()
    service = make_service(demo)
    service.repository = repository
    await service.recover()
    await service.arm()
    task = asyncio.create_task(service.run_once(execute=True))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        await service.disarm()
        release.set()
        run = await asyncio.wait_for(task, timeout=2)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    assert demo.place_calls == []
    assert run.results[0].order_submission_attempted is False
    assert "demo_automation_shared_control_unconfirmed" in run.results[0].detail
    assert service._state["armed"] is False
    assert service._state["emergency_stop"] is True
