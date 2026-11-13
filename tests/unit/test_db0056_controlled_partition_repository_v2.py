from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories import (
    account_history_partition_current56_v2 as repository,
)
from app.trade_qualification import native_controlled_account_source_v6 as native


def test_partition_cannot_be_constructed_from_source_only_data():
    with pytest.raises(native.ControlledAccountSourceError, match="source_unavailable"):
        repository.Db0056ControlledPartitionRepositoryV2(
            {"phase": "BANK", "db0056_roles": object(), "db0056_factories": object()}
        )


def test_partition_replay_scopes_open_and_write_to_original_bank(monkeypatch):
    at = datetime(2026, 11, 15, tzinfo=UTC)
    engine = object()
    factory = SimpleNamespace(kw={"bind": engine})
    selected = SimpleNamespace(writer_factory=factory)
    roles = object()
    scope = native.partition_policy.HistoryPartitionScopeV1("demo", "730011", "USDT")
    expected = SimpleNamespace(
        state=SimpleNamespace(
            scope=scope, arm_intent_current=lambda value: value == at
        ),
    )
    repo = object.__new__(repository.Db0056ControlledPartitionRepositoryV2)
    repo.session_factory = factory
    state = {
        "phase": "BANK",
        "coverage_observation": object(),
        "mirror_observation": object(),
        "control": {
            "ledger_scope": scope,
            "scope": scope,
            "observation": expected,
        },
        "history": object(),
        "facts": SimpleNamespace(scope=scope),
        "expires_at": at + timedelta(seconds=5),
    }
    monkeypatch.setattr(
        native, "_check_v6", lambda _: {"utc_ns": int(at.timestamp()) * 10**9}
    )
    monkeypatch.setattr(native, "_owned_database_0056_v6", lambda _: (selected, roles))
    monkeypatch.setattr(
        native.history,
        "_financial_state_v1",
        lambda _, **__: {"factory": SimpleNamespace(kw={"bind": engine})},
    )
    assert native._replay_owned_partition_repository_v6(state, repo, opening=True) == (
        selected,
        roles,
        scope,
        expected,
        at,
        state["expires_at"],
    )
    with pytest.raises(
        native.ControlledAccountSourceError,
        match="original_partition_repository_required",
    ):
        native._replay_owned_partition_repository_v6(state, repo, append=True)
    state["partition_repository"] = repo
    native._replay_owned_partition_repository_v6(state, repo, append=True)
    state["partition"] = object()
    with pytest.raises(
        native.ControlledAccountSourceError,
        match="original_partition_repository_required",
    ):
        native._replay_owned_partition_repository_v6(state, repo, append=True)
    native._replay_owned_partition_repository_v6(state, repo)
    repo.session_factory = object()
    with pytest.raises(
        native.ControlledAccountSourceError,
        match="original_partition_repository_required",
    ):
        native._replay_owned_partition_repository_v6(state, repo)
