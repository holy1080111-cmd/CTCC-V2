"""Actual synthetic B1 collector with explicit in-memory SQL adapters.

These cases verify the join and negative ownership boundary; they cannot prove
native transport, database isolation or a completed risk history.
"""

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
    BalanceSourcePage,
    ObservationBatchReadback,
)
from app.database.repositories.qualification_ledger import (
    PortfolioLocalCheckpoint,
    QualificationLedgerRepository,
)
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_materializer as mapping
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_portfolio_components as components
from app.trade_qualification import account_portfolio_runtime as runtime
from tests.unit.test_account_current_source_verifier import flat_pages
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_qualification_account_capture import NOW, row
from tests.unit.test_qualification_account_collector import SECRETS
from tests.unit.test_qualification_account_materializer import inputs
from tests.unit.test_qualification_account_v4 import script as v5_script
from tests.unit.test_qualification_bootstrap_runtime import unknown_state


def prepare(monkeypatch, *, local_problem=None):
    session, harness, checkpoints, args, events = setup(
        monkeypatch, states=[unknown_state()] * 3, v4=True
    )
    pages = flat_pages()
    pages["account_instruments"] = [[row("account_instruments", tickSz="0.1")]]
    harness.script = v5_script(pages=pages)
    reads, appended = [], []

    async def read_local(self, scope):
        data = {
            "environment": scope.environment,
            "account_id": scope.account_id,
            "requested_settlement_currency": scope.settlement_currency,
            "blocking_reasons": []
            if local_problem != "unknown"
            else ["legacy_reconciliation_initialization_unknown"],
            "synthetic_local_revision": int(local_problem == "changed" and bool(reads)),
        }
        result = PortfolioLocalCheckpoint(
            journal.canonical(data), harness.clock(), harness.clock()
        )
        reads.append(result)
        return result

    async def append(self, scope, reference, window, *, expected_revision):
        assert expected_revision == 0 and reference == observed.source_reference(
            tuple(events)
        )
        proof = observed.replay_observation_index(
            ((tuple(events), reference),), scope=scope, window=window
        )
        result = ObservationBatchReadback(1, "a" * 64, b"synthetic SQL adapter", proof)
        appended.append(result)
        return result

    async def read_page(
        self, scope, *, through_sequence, expected_head_sha256, after=0
    ):
        assert through_sequence == 1 and expected_head_sha256 == "a" * 64 and after == 0
        value = components.observe_balance(
            tuple(events),
            reference=observed.source_reference(tuple(events)),
            scope=scope,
        )
        return BalanceSourcePage(1, "a" * 64, 0, ((1, None, "a" * 64, value),))

    monkeypatch.setattr(
        QualificationLedgerRepository, "read_portfolio_checkpoint", read_local
    )
    monkeypatch.setattr(AccountObservationIndexRepository, "append", append)
    monkeypatch.setattr(
        AccountObservationIndexRepository, "read_balance_source_page", read_page
    )
    profile = inputs(instruments=(), costs=(), ledger=None, history=None, peak=None)
    args.update(
        observation_repository=AccountObservationIndexRepository(
            None, clock=harness.clock
        ),
        window=observed.ExecutionWindow(
            NOW - timedelta(days=7), NOW - timedelta(seconds=600)
        ),
        expected_revision=0,
        profile=profile,
        expected_profile_sha256=mapping.materialization_inputs_sha256(profile),
    )
    return session, harness, checkpoints, args, events, reads, appended


@pytest.mark.asyncio
@pytest.mark.parametrize("local_problem", [None, "changed", "unknown"])
async def test_real_synthetic_source_components_do_not_mint_account_or_native_owner(
    monkeypatch, local_problem
):
    session, harness, checkpoints, args, events, reads, appended = prepare(
        monkeypatch, local_problem=local_problem
    )
    result = await runtime.collect_owned_portfolio_components(session, **args)
    data = json.loads(result.receipt_json)
    assert data["schema_version"] == "ctcc.portfolio_components_diagnostic.v2"
    assert data["policy_sha256"] == runtime.DIAGNOSTIC_POLICY_SHA256
    assert len(checkpoints) == 3 and len(reads) == 2 and len(appended) == 1
    # The sealed v5 packet only queried four algo ordTypes. Its balance rows
    # remain recorded, but the incomplete current source cannot verify the
    # aggregate component or establish an empty exchange inventory.
    assert data["completed_components"]["balance_observation_verified"] is False
    assert data["completed_components"]["exchange_flat_inventory_verified"] is False
    assert "algo_type_coverage_incomplete" in data["blocking_reasons"]
    assert (
        data["completed_components"]["captured_contract_risk_specs_verified"] is False
    )
    assert {
        "captured_instrument_metadata_missing",
        "captured_instrument_product_unsupported",
    } <= set(data["blocking_reasons"])
    assert data["completed_components"]["sampled_hwm_population_verified"] is True
    assert data["completed_components"]["local_storage_flat_verified"] is (
        local_problem is None
    )
    assert data["completed_components"]["native_sampled_hwm_verified"] is False
    assert data["completed_components"]["native_current_clock_verified"] is False
    assert data["current_native_source_observed"] is False
    assert data["clock_evidence"]["current_owner_issued"] is False
    assert data["clock_evidence"]["native_clock_proof_sha256"] is None
    assert result.owner is result.snapshot is data["snapshot"] is None
    assert data["loss_history"] is data["loss_streak_at_history_start"] is None
    assert not data["account_complete"] and not data["account_revision_published"]
    assert not result.execution_authority and data["admission"] == "DENY"
    assert events and all(request.method == "GET" for request in harness.requests)
    assert all(secret.encode() not in result.receipt_json for secret in SECRETS)
    assert data["final_checked_at"] < data["component_expires_at"]
    harness.assert_closed()
    original_events = tuple(point.event for point in events)
    count = len(harness.requests)
    with pytest.raises(components.PortfolioComponentError):
        await runtime.collect_owned_portfolio_components(session, **args)
    assert len(harness.requests) == count
    assert tuple(point.event for point in events) == original_events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "caller_instrument",
        "caller_cost",
        "policy",
        "v1_policy",
        "profile_pin",
        "foreign_policy",
    ],
)
async def test_caller_evidence_or_unpinned_policy_rejected_before_any_account_io(
    monkeypatch, change
):
    session, harness, _, args, events, reads, appended = prepare(monkeypatch)
    callbacks = []
    if change == "caller_instrument":
        args["profile"] = inputs(costs=())
    elif change == "caller_cost":
        args["profile"] = inputs(instruments=())
    if change in ("caller_instrument", "caller_cost"):
        args["expected_profile_sha256"] = mapping.materialization_inputs_sha256(
            args["profile"]
        )
    elif change == "policy":
        args["expected_policy_sha256"] = "0" * 64
    elif change == "v1_policy":
        args["expected_policy_sha256"] = components.POLICY_SHA256
    elif change == "foreign_policy":

        class ForeignPin:
            def __eq__(self, other):
                callbacks.append("eq")
                return False

            def __ne__(self, other):
                callbacks.append("ne")
                return True

        args["expected_policy_sha256"] = ForeignPin()
    else:
        args["expected_profile_sha256"] = "0" * 64
    with pytest.raises(components.PortfolioComponentError):
        await runtime.collect_owned_portfolio_components(session, **args)
    assert not harness.requests and not events and not reads and not appended
    assert not callbacks


@pytest.mark.asyncio
async def test_legacy_native_tls_flags_never_upgrade_injected_clock_to_owner(
    monkeypatch,
):
    session, _, _, args, _, _, _ = prepare(monkeypatch)
    original_collect = runtime.bootstrap._collect_recorded
    original_measure = components.observe_balance

    async def claimed_native_tls(*arguments, **keywords):
        recorded, owner = await original_collect(*arguments, **keywords)
        # Deliberately adversarial legacy metadata seam; this is not native TLS.
        return (
            replace(
                recorded,
                bootstrap=replace(
                    recorded.bootstrap, transport_provenance="owned_signed_verified_tls"
                ),
            ),
            owner,
        )

    def claimed_recorded_tls(*arguments, **keywords):
        measured = original_measure(*arguments, **keywords)
        data = json.loads(measured.source_json)
        data["recorded_native_transport"] = True
        return components.BalanceMeasurement(journal.canonical(data))

    monkeypatch.setattr(runtime.bootstrap, "_collect_recorded", claimed_native_tls)
    monkeypatch.setattr(components, "observe_balance", claimed_recorded_tls)
    result = await runtime.collect_owned_portfolio_components(session, **args)
    data = json.loads(result.receipt_json)
    assert data["current_native_transport_observed"] is True
    assert data["measured_hwm"]["all_sources_recorded_native"] is True
    assert data["completed_components"]["sampled_hwm_original_tls_recorded"] is True
    assert data["completed_components"]["native_sampled_hwm_verified"] is False
    assert data["completed_components"]["native_current_clock_verified"] is False
    assert data["current_native_source_observed"] is False
    assert data["clock_evidence"]["historical_hwm_native_attestation"] == (
        "unknown_not_recorded_in_B1_v1"
    )
    assert "current_native_clock_attestation_missing" in data["blocking_reasons"]
    assert "hwm_original_native_clock_attestation_missing" in data["blocking_reasons"]
    assert result.owner is result.snapshot is None
    assert not result.execution_authority and data["admission"] == "DENY"
