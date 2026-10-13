"""Structural Demo rollout must observe exact-UID local state around reconcile."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from app.database.repositories.qualification_ledger import PortfolioLocalCheckpoint
from app.demo_automation import DemoAutomationSafetyError
from tests.unit.test_demo_automation import (
    FakeDemo,
    MemoryAutomationRepository,
    MemoryLocalCheckpointRepository,
    make_service,
    structural_dynamic_updates,
)


class ScenarioLocalCheckpointRepository(MemoryLocalCheckpointRepository):
    def __init__(self, changes: dict[int, Callable[[dict], None]]) -> None:
        super().__init__()
        self.changes = changes

    async def read_portfolio_checkpoint(self, scope) -> PortfolioLocalCheckpoint:
        point = await super().read_portfolio_checkpoint(scope)
        change = self.changes.get(len(self.scopes))
        if change is None:
            return point
        view = json.loads(point.document_json)
        change(view)
        changed = PortfolioLocalCheckpoint(
            document_json=json.dumps(view, sort_keys=True).encode(),
            observed_at=point.observed_at,
            received_at=point.received_at,
        )
        self.hashes[-1] = changed.state_sha256
        return changed


def _service(demo, repository):
    return make_service(
        demo,
        local_checkpoint_repository=repository,
        okx_demo_score_risk_enabled=True,
        okx_demo_max_open_positions=3,
        **structural_dynamic_updates(),
    )


@pytest.mark.asyncio
async def test_initialized_flat_uid_can_arm_across_expected_sync_time_update() -> None:
    demo = FakeDemo()
    repository = MemoryLocalCheckpointRepository()
    service = _service(demo, repository)
    await service.recover()

    result = await service.arm()

    assert result.armed is True
    assert demo.reconcile_calls == 1
    assert len(repository.scopes) == 2
    assert {scope.account_id for scope in repository.scopes} == {demo.account_uid}
    assert {scope.settlement_currency for scope in repository.scopes} == {"USDT"}
    # Reconcile may legitimately advance the legacy sync timestamp.
    assert repository.hashes[0] != repository.hashes[1]
    assert demo.place_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("uid", ("", "not-a-uid"))
async def test_missing_or_invalid_exact_uid_blocks_before_reconcile(uid: str) -> None:
    demo = FakeDemo()
    demo.account_uid = uid
    repository = MemoryLocalCheckpointRepository()
    service = _service(demo, repository)
    await service.recover()

    with pytest.raises(
        DemoAutomationSafetyError, match="structural_rollout_local_flat_unconfirmed"
    ):
        await service.arm()

    assert repository.scopes == []
    assert demo.reconcile_calls == 0
    assert (await service.status()).armed is False


@pytest.mark.asyncio
async def test_reconcile_uid_change_blocks_arming() -> None:
    demo = FakeDemo()
    demo.reconcile_uid = "987654321"
    repository = MemoryLocalCheckpointRepository()
    service = _service(demo, repository)
    await service.recover()

    with pytest.raises(
        DemoAutomationSafetyError, match="structural_rollout_account_uid_changed"
    ):
        await service.arm()

    assert demo.reconcile_calls == 1
    assert len(repository.scopes) == 1
    assert (await service.status()).armed is False


@pytest.mark.asyncio
async def test_reconcile_main_uid_change_blocks_arming() -> None:
    demo = FakeDemo()
    demo.reconcile_main_uid = "987654321"
    repository = MemoryLocalCheckpointRepository()
    service = _service(demo, repository)
    await service.recover()

    with pytest.raises(
        DemoAutomationSafetyError, match="structural_rollout_account_identity_changed"
    ):
        await service.arm()

    assert demo.reconcile_calls == 1
    assert len(repository.scopes) == 1
    assert (await service.status()).armed is False


@pytest.mark.asyncio
async def test_unpersisted_reconcile_blocks_before_second_local_read() -> None:
    demo = FakeDemo()
    demo.reconcile_persisted = False
    repository = MemoryLocalCheckpointRepository()
    service = _service(demo, repository)
    await service.recover()

    with pytest.raises(
        DemoAutomationSafetyError, match="structural_rollout_reconcile_not_persisted"
    ):
        await service.arm()

    assert demo.reconcile_calls == 1
    assert len(repository.scopes) == 1
    assert (await service.status()).armed is False


@pytest.mark.asyncio
async def test_reconcile_without_pinned_credential_session_blocks_arm() -> None:
    demo = FakeDemo()
    demo.reconcile_binding_id = None
    repository = MemoryLocalCheckpointRepository()
    service = _service(demo, repository)
    await service.recover()

    with pytest.raises(
        DemoAutomationSafetyError,
        match="structural_rollout_credential_session_unconfirmed",
    ):
        await service.arm()

    assert len(repository.scopes) == 1
    assert (await service.status()).armed is False


@pytest.mark.asyncio
async def test_mirror_overwrite_with_other_session_blocks_arm() -> None:
    demo = FakeDemo()
    repository = ScenarioLocalCheckpointRepository(
        {
            2: lambda view: view["legacy"]["sync_checkpoint"].__setitem__(
                "credential_session_binding_id", "b" * 64
            )
        }
    )
    service = _service(demo, repository)
    await service.recover()

    with pytest.raises(
        DemoAutomationSafetyError, match="structural_rollout_reconcile_binding_changed"
    ):
        await service.arm()

    assert len(repository.scopes) == 2
    assert (await service.status()).armed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_structural", (False, True))
async def test_structural_mode_change_during_reconcile_denies_arm(
    initial_structural: bool,
) -> None:
    service = None

    class SwitchingDemo(FakeDemo):
        async def reconcile(self):
            assert service is not None
            service.settings.okx_demo_structural_dynamic_leverage_enabled = (
                not initial_structural
            )
            return await super().reconcile()

    demo = SwitchingDemo()
    repository = MemoryLocalCheckpointRepository()
    if initial_structural:
        service = _service(demo, repository)
    else:
        service = make_service(demo, local_checkpoint_repository=repository)
    await service.recover()

    with pytest.raises(
        DemoAutomationSafetyError, match="demo_automation_arm_policy_changed"
    ):
        await service.arm()

    assert demo.reconcile_calls == 1
    assert len(repository.scopes) == (2 if initial_structural else 0)
    assert (await service.status()).armed is False


@pytest.mark.asyncio
async def test_policy_change_after_arm_commit_durably_latches_stop() -> None:
    service = None

    class SwitchingRepository(MemoryAutomationRepository):
        async def save_state(self, state):
            await super().save_state(state)
            if state["armed"]:
                assert service is not None
                service.settings.okx_demo_structural_dynamic_leverage_enabled = True

    repository = SwitchingRepository()
    service = make_service(FakeDemo())
    service.repository = repository
    await service.recover()

    with pytest.raises(
        DemoAutomationSafetyError, match="demo_automation_arm_policy_changed"
    ):
        await service.arm()

    status = await service.status()
    assert status.armed is False
    assert status.emergency_stop is True
    assert status.recovered is False
    assert repository.state["armed"] is False
    assert repository.state["emergency_stop"] is True
    assert repository.state["_restart_latch_required"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("blocker", "change"),
    (
        (
            "all_currency_local_holds_unresolved",
            lambda view: view["currency_states"][0]["active"].append(
                {"state": "reserved"}
            ),
        ),
        (
            "all_currency_local_intents_unresolved",
            lambda view: view["unresolved_intents"].append(["reservation", "consumed"]),
        ),
    ),
)
async def test_durable_hold_or_intent_blocks_before_exchange_reconcile(
    blocker: str, change: Callable[[dict], None]
) -> None:
    demo = FakeDemo()

    def blocked(view):
        change(view)
        view["blocking_reasons"] = [blocker]

    repository = ScenarioLocalCheckpointRepository({1: blocked})
    service = _service(demo, repository)
    await service.recover()

    with pytest.raises(
        DemoAutomationSafetyError, match="structural_rollout_local_flat_unconfirmed"
    ):
        await service.arm()

    assert demo.reconcile_calls == 0
    assert len(repository.scopes) == 1
    assert (await service.status()).armed is False


@pytest.mark.asyncio
async def test_local_ledger_change_during_reconcile_blocks_arming() -> None:
    demo = FakeDemo()

    def advance_ledger(view):
        view["currency_states"][0]["ledger_revision"] = 1

    repository = ScenarioLocalCheckpointRepository({2: advance_ledger})
    service = _service(demo, repository)
    await service.recover()

    with pytest.raises(
        DemoAutomationSafetyError, match="structural_rollout_local_checkpoint_changed"
    ):
        await service.arm()

    assert demo.reconcile_calls == 1
    assert len(repository.scopes) == 2
    assert (await service.status()).armed is False


@pytest.mark.asyncio
async def test_new_uncertain_hold_during_reconcile_blocks_arming() -> None:
    demo = FakeDemo()

    def new_hold(view):
        view["currency_states"][0]["active"].append({"state": "uncertain"})
        view["blocking_reasons"] = ["all_currency_local_holds_unresolved"]

    repository = ScenarioLocalCheckpointRepository({2: new_hold})
    service = _service(demo, repository)
    await service.recover()

    with pytest.raises(
        DemoAutomationSafetyError, match="structural_rollout_local_flat_unconfirmed"
    ):
        await service.arm()

    assert demo.reconcile_calls == 1
    assert len(repository.scopes) == 2
    assert (await service.status()).armed is False


@pytest.mark.asyncio
async def test_checkpoint_cannot_claim_future_writer_exclusion() -> None:
    demo = FakeDemo()

    def forged_authority(view):
        view["future_writer_exclusion"] = True

    repository = ScenarioLocalCheckpointRepository({1: forged_authority})
    service = _service(demo, repository)
    await service.recover()

    with pytest.raises(
        DemoAutomationSafetyError, match="structural_rollout_local_flat_unconfirmed"
    ):
        await service.arm()

    assert demo.reconcile_calls == 0
    assert (await service.status()).armed is False


@pytest.mark.asyncio
async def test_missing_local_checkpoint_repository_blocks_structural_arm() -> None:
    demo = FakeDemo()
    service = _service(demo, MemoryLocalCheckpointRepository())
    service.local_checkpoint_repository = None
    await service.recover()

    with pytest.raises(
        DemoAutomationSafetyError, match="structural_rollout_local_flat_unconfirmed"
    ):
        await service.arm()

    assert demo.reconcile_calls == 0
    assert (await service.status()).armed is False
