"""Restart inventory uses the replayed V7 page counts and exact local endpoints."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories.qualification_ledger import PortfolioLocalCheckpoint
from app.demo_automation import native_restart_inventory_0056_v1 as subject
from app.domain.source_primitives import canonical
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_v4 as source
from tests.unit.test_account_current_source_v4 import setup


def _checkpoint(plan, at, *, blockers=()):
    return PortfolioLocalCheckpoint(
        canonical(
            {
                "schema_version": "ctcc.portfolio_local_checkpoint.v2",
                "environment": "demo",
                "account_id": plan.expected_uid,
                "requested_settlement_currency": plan.settlement_currency,
                "future_writer_exclusion": False,
                "execution_authority": False,
                "blocking_reasons": list(blockers),
                "currency_states": [{"account_revision": 2}],
            }
        ),
        at,
        at + timedelta(milliseconds=1),
    )


async def test_restart_report_requires_original_v7_counts_and_keeps_unknown(
    monkeypatch, tmp_path
):
    with setup(monkeypatch, tmp_path) as original:
        owned = await source.acquire_initial_demo_account_source_v4(**original.args)
        operands = source.consume_owned_current_demo_account_v4(owned)
        plan = operands.packet.plan
        assert type(plan) is capture.CurrentDemoAccountCapturePlanV7
        public = {
            "packet_sha256": operands.reference.packet_sha256,
            "original_chain_sha256": operands.reference.head_sha256,
            "current_source_receipt_sha256": subject.sha(operands.current_receipt_json),
            "execution_authority": False,
        }
        now = operands.packet.completed_at
        unknown = subject._inventory_document(public, operands, plan, None, None)
        assert unknown["local_exposure_observation"] == "UNKNOWN"
        assert unknown["local_checkpoint_blocker_count"] is None
        assert unknown["exchange_observed_positions"] == 0
        assert unknown["exchange_observed_ordinary_orders"] == 0
        assert len(unknown["exchange_observed_algo_order_rows_by_query"]) == 8
        assert (
            unknown["active_protection_verified"]
            is unknown["flat_start_permission"]
            is False
        )
        before = _checkpoint(plan, now, blockers=("scope_unknown",))
        after = replace(
            before,
            observed_at=before.observed_at + timedelta(seconds=1),
            received_at=before.received_at + timedelta(seconds=1),
        )
        recovered = subject._inventory_document(public, operands, plan, before, after)
        assert recovered["local_exposure_observation"] == "INCOMPLETE_OR_EXPOSED"
        assert recovered["local_checkpoint_blocker_count"] == 1
        assert recovered["local_checkpoint_sha256"] == after.state_sha256
        assert recovered["execution_authority"] is False
        assert "uid" not in recovered and "account_id" not in recovered
        with pytest.raises(
            subject.current.NativeCurrentSource0056Error, match="local_exposure_changed"
        ):
            subject._inventory_document(
                public, operands, plan, before, _checkpoint(plan, after.observed_at)
            )
        with pytest.raises(
            subject.current.NativeCurrentSource0056Error, match="exchange_readback"
        ):
            subject._inventory_document(
                {**public, "original_chain_sha256": "0" * 64},
                operands,
                plan,
                None,
                None,
            )


def test_current_restart_cli_modes_are_exclusive(monkeypatch):
    from scripts import observe_demo_current_source_0056 as cli

    async def restart(instrument):
        return SimpleNamespace(
            database_head="0056",
            receipt_sha256="a" * 64,
            account_complete=False,
            execution_authority=False,
            admission="DENY",
        )

    monkeypatch.setattr(cli, "observe_configured_restart_inventory_0056_v1", restart)
    assert cli.main(["--instrument", "BTC-USDT-SWAP", "--restart-inventory"]) == 0
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "--instrument",
                "BTC-USDT-SWAP",
                "--restart-inventory",
                "--include-history",
            ]
        )
    assert error.value.code == 2
