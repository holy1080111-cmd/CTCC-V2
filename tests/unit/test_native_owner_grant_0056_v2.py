"""One approved DB0056 grant attempt and reconcile-only boundaries."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import native_owner_grant_0056_v2 as tool
from scripts.native_owner_grant_confirmation_0056_v1 import (
    OwnerGrantConfirmationRefusal,
)
from tests.unit.test_native_owner_grant_0056_draft_v1 import _pins


@pytest.fixture
def staged(monkeypatch):
    pins = _pins()
    options = {
        "plan_file": Path("plan"),
        "plan_file_sha256": "a" * 64,
        "owner_dsn_file": Path("owner"),
        "ca_file": Path("ca"),
        "source_archive_file": Path("source"),
        "journal_directory": Path("journal"),
    }
    receipt = {
        "status": "OBSERVED_DENY",
        "admission": "DENY",
        "target_grant_pins_sha256": tool._plan_sha256(pins),
        "restricted_logins_observed": 3,
        "startup_admission": False,
        "execution_authority": False,
        "external_approval_authenticated": False,
    }
    engine = SimpleNamespace(dispose=AsyncMock())
    journal = SimpleNamespace(
        created=True,
        status="STARTED",
        record_terminal=Mock(),
        record_reconciliation=Mock(return_value="b" * 64),
    )
    prepared = SimpleNamespace(
        url=object(),
        tls=object(),
        route_sha256="route",
        manifest_sha256="manifest",
        migration_sha256="migration",
    )
    monkeypatch.setattr(tool, "_supports_durable_grant_journal", lambda: True)
    monkeypatch.setattr(tool, "_checked_source", Mock())
    monkeypatch.setattr(tool, "_check_restricted_settings", AsyncMock())
    monkeypatch.setattr(tool.target, "_require_private_file", Mock())
    monkeypatch.setattr(tool.target, "require_private_evidence_base_v2", Mock())
    monkeypatch.setattr(tool, "prepare_route", Mock(return_value=prepared))
    monkeypatch.setattr(
        tool.selectors, "_database_route", Mock(return_value=tool._target_route(pins))
    )
    monkeypatch.setattr(tool.readback, "_load_plan", Mock(return_value=pins))
    monkeypatch.setattr(
        tool,
        "preflight_owner_route_0056_v1",
        AsyncMock(
            return_value={
                "status": "PASS",
                "database_head": "0056",
                "route_sha256": prepared.route_sha256,
                "startup_admission": False,
                "execution_authority": False,
            }
        ),
    )
    confirmation = Mock()
    monkeypatch.setattr(tool, "confirm_owner_grant_0056_v1", confirmation)
    monkeypatch.setattr(tool, "create_async_engine", Mock(return_value=engine))
    monkeypatch.setattr(tool, "async_sessionmaker", Mock())
    monkeypatch.setattr(tool.acl, "_read_before", AsyncMock(return_value={}))
    commit = AsyncMock()
    monkeypatch.setattr(tool.acl, "_commit_once", commit)
    load_existing = Mock(return_value=None)
    begin = Mock(return_value=journal)
    monkeypatch.setattr(tool.journals, "load_existing", load_existing)
    monkeypatch.setattr(tool.journals, "load_or_begin", begin)
    read = AsyncMock(return_value=receipt)
    monkeypatch.setattr(tool.readback, "observe_owner_postgrant_readback_0056_v1", read)
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_one_attempt_confirms_before_journal_commit_and_three_login_readback(
    staged,
):
    calls = Mock()
    for name in ("confirmation", "begin", "commit", "read"):
        calls.attach_mock(getattr(staged, name), name)
    outcome = await tool.apply_attended_owner_grant_0056_v2(**staged.options)
    assert [call[0] for call in calls.mock_calls] == [
        "confirmation",
        "begin",
        "commit",
        "read",
    ]
    staged.confirmation.assert_called_once_with(staged.options["plan_file_sha256"])
    tool.acl._read_before.assert_awaited_once_with(
        tool.async_sessionmaker.return_value, staged.pins, reviewed=True
    )
    assert outcome["status"] == "GRANTED_AND_OBSERVED"
    assert outcome["grant_statements"] == 42
    assert outcome["admission"] == "DENY"
    assert outcome["external_approval_authenticated"] is False
    staged.journal.record_terminal.assert_called_once_with("OBSERVED_GRANTED")


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["unconfirmed", "attempt", "platform"])
async def test_refused_before_mutating(staged, monkeypatch, blocked):
    expected = {
        "unconfirmed": OwnerGrantConfirmationRefusal,
        "attempt": tool.OwnerGrantAttemptNeedsReconciliation,
        "platform": tool.acl.OwnerGrantRefusal,
    }
    if blocked == "unconfirmed":
        staged.confirmation.side_effect = OwnerGrantConfirmationRefusal
    elif blocked == "attempt":
        staged.load_existing.return_value = staged.journal
    else:
        monkeypatch.setattr(tool, "_supports_durable_grant_journal", lambda: False)
    with pytest.raises(expected[blocked]):
        await tool.apply_attended_owner_grant_0056_v2(**staged.options)
    staged.begin.assert_not_called()
    staged.commit.assert_not_awaited()
    staged.read.assert_not_awaited()
    if blocked in {"attempt", "platform"}:
        staged.confirmation.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["COMMIT_UNKNOWN", "POSTCOMMIT_INCOMPLETE"])
async def test_commit_or_readback_failure_blocks_reentry(staged, outcome):
    if outcome == "COMMIT_UNKNOWN":
        expected = tool.acl.OwnerGrantCommitUnknown
        staged.commit.side_effect = expected
    else:
        expected = tool.acl.OwnerGrantPostcommitIncomplete
        staged.read.side_effect = RuntimeError
    with pytest.raises(expected):
        await tool.apply_attended_owner_grant_0056_v2(**staged.options)
    staged.journal.record_terminal.assert_called_once_with(outcome)
    staged.load_existing.return_value = staged.journal
    with pytest.raises(tool.OwnerGrantAttemptNeedsReconciliation):
        await tool.apply_attended_owner_grant_0056_v2(**staged.options)
    staged.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_only_reads_exact_grants_without_assuming_attribution(staged):
    staged.load_existing.return_value = staged.journal
    result = await tool.reconcile_owner_grant_0056_v2(**staged.options)
    assert result["status"] == "EXACT_GRANTED_ACL_OBSERVED"
    assert result["commit_attributed_to_attempt"] is False
    staged.journal.record_reconciliation.assert_called_once_with(
        "EXACT_GRANTED_ACL_OBSERVED"
    )
    staged.confirmation.assert_not_called()
    staged.commit.assert_not_called()
    staged.begin.assert_not_called()
