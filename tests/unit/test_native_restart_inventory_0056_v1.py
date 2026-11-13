"""Restart inventory uses the replayed V7 page counts and exact local endpoints."""

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories.qualification_ledger import PortfolioLocalCheckpoint
from app.demo_automation import native_restart_inventory_0056_v1 as subject
from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_v4 as source
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import native_protection_observation_0056_v1 as protection
from tests.unit.test_account_current_source_v4 import setup
from tests.unit.test_account_current_source_v7 import (
    empty_v7_pages,
    recorded_v7,
    verify_v7,
)
from tests.unit.test_qualification_account_capture import row


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
        assert unknown["exchange_position_bracket_observations"] == {
            "structural_match_count": 0,
            "position_rows_omitted": 0,
            "positions": [],
        }
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


def _position_and_oco():
    pages = empty_v7_pages()
    position = row("positions", margin="5", notionalUsd="200", markPx="101")
    pages["account_position_risk"][0][0]["posData"] = [
        {
            key: position[key]
            for key in (
                "posId",
                "instId",
                "instType",
                "posSide",
                "mgnMode",
                "pos",
                "ccy",
            )
        }
    ]
    pages["positions"] = [[position]]
    pages["algo_oco"] = [
        [
            row(
                "algo_oco",
                algoClOrdId="SyntheticOco1",
                ccy="USDT",
                reduceOnly="true",
                slTriggerPx="95",
                slOrdPx="-1",
                slTriggerPxType="mark",
                tpTriggerPx="120",
                tpOrdPx="-1",
                tpTriggerPxType="mark",
            )
        ],
        [],
    ]
    return pages


async def _replay_inventory(monkeypatch, pages):
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    reference = observed.source_reference(chain)
    packet = capture.verify_demo_account_packet(
        next(item.event.packet_payload for item in chain if item.event.packet_payload),
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )
    receipt = verify_v7(chain).receipt_json
    return packet, reference, receipt


async def test_restart_reports_matching_exchange_bracket_fingerprints_without_authority(
    monkeypatch,
):
    packet, reference, receipt = await _replay_inventory(
        monkeypatch, _position_and_oco()
    )
    public = {
        "packet_sha256": reference.packet_sha256,
        "original_chain_sha256": reference.head_sha256,
        "current_source_receipt_sha256": sha(receipt),
        "execution_authority": False,
    }
    operands = SimpleNamespace(packet=packet, current_receipt_json=receipt)
    result = subject._inventory_document(public, operands, packet.plan, None, None)
    source_rows = {item["stream"]: item for item in json.loads(receipt)["current_rows"]}
    observed_brackets = result["exchange_position_bracket_observations"]
    assert observed_brackets == {
        "structural_match_count": 1,
        "position_rows_omitted": 0,
        "positions": [
            {
                "status": "OBSERVED_FULL_BRACKET_UNATTRIBUTED",
                "position_row_sha256": source_rows["positions"]["row_sha256"],
                "pending_oco_row_sha256": source_rows["algo_oco"]["row_sha256"],
            }
        ],
    }
    assert all(
        marker not in canonical(observed_brackets)
        for marker in (b"SyntheticOco1", b"-SWAP", b"instId", b"algoId", b"posId")
    )
    assert result["emergency_stop"] is True
    assert (
        result["active_protection_verified"]
        is result["position_attribution_complete"]
        is result["unresolved_intents_cleared"]
        is result["flat_start_permission"]
        is result["execution_authority"]
        is False
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "pause"},
        {"side": "buy"},
        {"ccy": "USDC"},
        {"reduceOnly": True},
        {"sz": "3"},
        {"closeFraction": "1"},
        {"tpTriggerPxType": "last"},
        {"tpTriggerPx": "100"},
        {"actualSz": "1"},
    ],
)
async def test_pending_bracket_mismatch_stays_unknown(monkeypatch, changes):
    pages = _position_and_oco()
    pages["algo_oco"][0][0].update(changes)
    packet, _, receipt = await _replay_inventory(monkeypatch, pages)
    result = protection.observe_position_brackets(packet, json.loads(receipt))
    assert result["structural_match_count"] == 0
    assert result["positions"][0]["status"] == "UNKNOWN"
    assert result["positions"][0]["pending_oco_row_sha256"] is None


@pytest.mark.parametrize("duplicate_client", [False, True])
async def test_overlapping_row_or_reused_client_stays_unknown(
    monkeypatch, duplicate_client
):
    pages = _position_and_oco()
    pages["algo_conditional"] = [
        [
            row(
                "algo_conditional",
                algoClOrdId="SyntheticOco1" if duplicate_client else "OtherClient",
                **({"instId": "ETH-USDT-SWAP"} if duplicate_client else {}),
            )
        ],
        [],
    ]
    packet, _, receipt = await _replay_inventory(monkeypatch, pages)
    result = protection.observe_position_brackets(packet, json.loads(receipt))
    assert result["positions"][0]["status"] == "UNKNOWN"


async def test_receipt_witness_change_is_refused(monkeypatch):
    packet, _, receipt = await _replay_inventory(monkeypatch, _position_and_oco())
    source = json.loads(receipt)
    pending_algo = next(
        item for item in source["current_rows"] if item["stream"] == "algo_oco"
    )
    pending_algo["row_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="db0056_restart_bracket_source_changed"):
        protection.observe_position_brackets(packet, source)


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
