"""Synthetic eight-algo current replay; no authenticated account authority."""

import json
from datetime import timedelta

import pytest

from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.test_account_current_source_verifier import SCOPE, flat_pages
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_qualification_account_capture import NOW, row, wire
from tests.unit.test_qualification_account_capture import observe as observe_page
from tests.unit.test_qualification_account_capture import verify as verify_packet
from tests.unit.test_qualification_account_collector import credentials
from tests.unit.test_qualification_account_v4 import plan as historical_plan
from tests.unit.test_qualification_account_v4 import records as packet_records
from tests.unit.test_qualification_account_v4 import script as account_script


def current_plan_v7(**changes):
    values = capture._plain(historical_plan())
    values.update(
        contract_version="ctcc.demo_current_account_plan.v7",
        capture_scope="all_current_standard_products_v7_eight_algos",
        **changes,
    )
    return capture.CurrentDemoAccountCapturePlanV7(**values)


def empty_v7_pages():
    pages = flat_pages()
    for kind in capture.CURRENT_ALGO_ORDER_TYPES_V7:
        pages[f"algo_{kind}"] = [[]]
    return pages


async def recorded_v7(
    monkeypatch, *, pages=None, plan_changes=None, states=None, offset_seconds=1
):
    session, harness, _, args, events = setup(monkeypatch, states=states)
    selected = current_plan_v7(**(plan_changes or {}))
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=selected.session_binding_id),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    harness.script = account_script(
        source=empty_v7_pages() if pages is None else pages,
        streams=capture.V7_CURRENT_STREAMS,
    )
    harness.clock.overrides.update(
        {
            index: NOW + timedelta(seconds=offset_seconds, milliseconds=index + 1)
            for index in range(2000)
        }
    )
    await bootstrap.collect_bootstrap_recorded(session, **args)
    harness.assert_closed()
    return tuple(events), harness


def verify_v7(chain, *, validated_at=NOW + timedelta(seconds=4)):
    return current.verify_current_account_sources(
        chain,
        reference=observed.source_reference(chain),
        scope=SCOPE,
        validated_at=validated_at,
        expected_policy_sha256=current.V7_POLICY_SHA256,
    )


def test_v7_policy_is_separate_from_sealed_four_algo_policies():
    assert current.POLICY_SHA256 == (
        "0c1210d6021069a7c7e47ba2481fdca1ce0b379fbd00f412e682c32e696e121a"
    )
    assert current.V6_POLICY_SHA256 == (
        "9b85f9e5169713d95dae67437a6bbe5da4208a97cf5d6a59393e7f37d237971d"
    )
    policy = json.loads(current.V7_POLICY_BYTES)
    assert policy["version"] == "ctcc.current_account_source_policy.v5"
    assert policy["capture_plan_contract"] == "ctcc.demo_current_account_plan.v7"
    assert policy["current_streams"] == list(capture.V7_CURRENT_STREAMS)
    assert policy["algo_order_types"] == list(capture.CURRENT_ALGO_ORDER_TYPES_V7)
    assert len(policy["algo_order_types"]) == 8
    assert current.V7_POLICY_SHA256 not in {
        current.POLICY_SHA256,
        current.V6_LEGACY_POLICY_SHA256,
        current.V6_POLICY_SHA256,
    }


@pytest.mark.asyncio
async def test_v7_all_eight_empty_terminal_chains_can_observe_empty_only(monkeypatch):
    chain, harness = await recorded_v7(monkeypatch)
    result = verify_v7(chain)
    value = json.loads(result.receipt_json)
    assert value["schema_version"] == "ctcc.current_account_source_observation.v6"
    assert value["policy_sha256"] == current.V7_POLICY_SHA256
    assert value["effective_policy_sha256"] == current.V7_POLICY_SHA256
    assert "algo_coverage_revocation_sha256" not in value
    assert value["packet_schema_version"] == "ctcc.demo_current_account_capture.v7"
    assert value["observation_interval"]["freshness_basis"] == (
        "replay_verified_B1_body_complete_EOF"
    )
    assert value["blocking_reasons"] == []
    assert value["observed_flat"] is True
    assert set(value["inventory_row_counts"]) == {
        "positions",
        "orders_pending",
        *(f"algo_{kind}" for kind in capture.CURRENT_ALGO_ORDER_TYPES_V7),
    }
    assert all(count == 0 for count in value["inventory_row_counts"].values())
    algo_pages = [
        page for page in value["current_pages"] if page["stream"].startswith("algo_")
    ]
    assert len(algo_pages) == 8
    assert value["algo_type_coverage"]["all_eight_terminal_chains_replayed"] is True
    assert len(value["algo_type_coverage"]["terminal_receipt_sha256s"]) == 8
    assert all(
        page["terminal"] is True and page["row_count"] == 0 for page in algo_pages
    )
    assert value["flat_start_permission"] is result.flat_start_permission is False
    assert value["account_complete"] is result.account_complete is False
    assert value["execution_authority"] is result.execution_authority is False
    assert value["admission"] == "DENY"
    assert len(harness.requests) == len(value["current_pages"])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["chase", "iceberg", "twap", "smart_iceberg"])
async def test_v7_newly_queried_algo_type_is_real_exposure_not_flat(monkeypatch, kind):
    pages = empty_v7_pages()
    pages[f"algo_{kind}"] = [[row(f"algo_{kind}")], []]
    chain, _ = await recorded_v7(monkeypatch, pages=pages)
    value = json.loads(verify_v7(chain).receipt_json)
    assert value["inventory_row_counts"][f"algo_{kind}"] == 1
    assert value["observed_flat"] is False
    assert (
        "current_exposure_requires_protection_and_local_join"
        in value["blocking_reasons"]
    )
    assert value["current_rows"]
    assert value["account_complete"] is value["execution_authority"] is False


@pytest.mark.asyncio
async def test_v7_expired_or_relabelled_source_cannot_observe_flat(monkeypatch):
    chain, _ = await recorded_v7(monkeypatch)
    stale = json.loads(
        verify_v7(chain, validated_at=NOW + timedelta(seconds=35)).receipt_json
    )
    assert stale["observed_flat"] is False
    assert "measured_current_receipt_stale" in stale["blocking_reasons"]
    with pytest.raises(current.CurrentAccountSourceError):
        current.verify_current_account_sources(
            chain,
            reference=observed.source_reference(chain),
            scope=SCOPE,
            validated_at=NOW + timedelta(seconds=4),
            expected_policy_sha256=current.V6_POLICY_SHA256,
        )
    with pytest.raises(current.CurrentAccountSourceError):
        verify_v7(chain[:-1])


def test_v7_missing_algo_terminal_or_wrong_algo_id_cursor_fails_closed():
    pages = empty_v7_pages()
    pages["algo_chase"] = [[row("algo_chase")], []]
    selected, observations = packet_records(
        selected=current_plan_v7(), pages=pages, streams=capture.V7_CURRENT_STREAMS
    )
    assert verify_packet(selected, observations).schema_version == (
        "ctcc.demo_current_account_capture.v7"
    )
    terminal_index = next(
        index
        for index, page in enumerate(observations)
        if page.request.stream == "algo_chase" and page.page_index == 1
    )
    with pytest.raises(
        capture.AccountCaptureError, match="empty_terminal_page_required"
    ):
        verify_packet(
            selected,
            observations[:terminal_index] + observations[terminal_index + 1 :],
        )
    terminal = observations[terminal_index]
    wrong_cursor = observe_page(
        wire([]),
        selected=selected,
        stream="algo_chase",
        page_index=1,
        after="999",
        previous_page_sha256=terminal.previous_page_sha256,
        identity_receipt_sha256=terminal.identity_receipt_sha256,
        request_started_at=terminal.request_started_at,
        headers_received_at=terminal.headers_received_at,
        body_completed_at=terminal.body_completed_at,
    )
    with pytest.raises(capture.AccountCaptureError, match="page_chain_mismatch"):
        verify_packet(
            selected,
            observations[:terminal_index]
            + (wrong_cursor,)
            + observations[terminal_index + 1 :],
        )
