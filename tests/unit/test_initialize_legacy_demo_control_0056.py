from datetime import UTC, datetime, timedelta

import pytest

from app.database.models.demo_automation import DemoAutomationState
from app.database.repositories import native_legacy_control_genesis_0056_v1 as genesis
from scripts import initialize_legacy_demo_control_0056 as cli


def test_legacy_genesis_readback_requires_exact_stopped_event_and_fields():
    at = datetime(2026, 9, 16, 14, 0, tzinfo=UTC)
    event = "a" * 64
    record = DemoAutomationState(**genesis._stopped_values(at, event))
    assert record.session_date == at.date()
    assert record.armed is False
    assert (
        record.locked is record.emergency_stop is record.restart_latch_required is True
    )
    assert (
        record.equity_basis is record.baseline_equity is record.risk_peak_equity is None
    )
    assert genesis._exact_readback([record], at, event) == at
    for rows, observed, selector in (
        ([], at, event),
        ([record, record], at, event),
        ([record], at - timedelta(microseconds=1), event),
        ([record], at, "b" * 64),
    ):
        with pytest.raises(genesis.LegacyControlGenesis0056Error):
            genesis._exact_readback(rows, observed, selector)
    record.realized_pnl_events = [{"unexpected": True}]
    with pytest.raises(genesis.LegacyControlGenesis0056Error):
        genesis._exact_readback([record], at, event)


def test_operator_confirmation_binds_plan_descriptor_stop_and_source():
    pins = ["1" * 64, "2" * 64, "3" * 64, "4" * 40]
    phrase = cli._confirmation(*pins)
    assert phrase.startswith("CTCC_DB0056_CREATE_STOPPED_LEGACY ")
    for i, old in enumerate(pins):
        changed = pins.copy()
        changed[i] = "5" * len(old)
        assert cli._confirmation(*changed) != phrase


def test_cli_redacts_refusals_and_uses_distinct_uncertain_status(monkeypatch, capsys):
    args = [
        "--owner-plan-file",
        "private-plan",
        "--owner-dsn-file",
        "private-dsn",
        "--descriptor-file",
        "private-descriptor",
        "--source-archive-file",
        "source",
        "--owner-plan-sha256",
        "1" * 64,
        "--descriptor-sha256",
        "2" * 64,
        "--control-event-sha256",
        "3" * 64,
    ]

    async def should_not_reach(**_):
        pytest.fail("invalid command reached the database logic")

    monkeypatch.setattr(cli, "_run", should_not_reach)
    assert cli.main(args) == 1
    output = capsys.readouterr()
    assert '"status":"REFUSED"' in output.out
    assert str(args[3]) not in output.out + output.err

    async def lost_receipt(**arguments):
        assert arguments["reconcile_only"] is False
        raise genesis.LegacyControlGenesis0056Uncertain("private-value")

    monkeypatch.setattr(cli, "_run", lost_receipt)
    assert cli.main([*args, "--initialize-stopped"]) == 1
    output = capsys.readouterr()
    assert '"status":"STOPPED_COMMIT_UNCERTAIN_RECONCILE_ONLY"' in output.out
    assert '"execution_authority":false' in output.out
    assert "private-value" not in output.out + output.err
