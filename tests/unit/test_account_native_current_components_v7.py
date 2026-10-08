"""Pure V7 projection tests; these synthetic sources cannot mint native leases."""

import json
from datetime import timedelta

import pytest

from app.domain.source_primitives import sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_current_components as components
from app.trade_qualification import account_native_runtime as native
from app.trade_qualification import account_observation_index as observed
from tests.unit.test_account_current_source_v7 import empty_v7_pages
from tests.unit.test_qualification_account_capture import NOW, row
from tests.unit.test_qualification_account_capture_v7 import v7_observations, v7_plan


def synthetic_projected_v7(*, exposed_stream=None):
    selected = v7_plan()
    pages = empty_v7_pages()
    if exposed_stream is not None:
        pages[exposed_stream] = [[row(exposed_stream)], []]
    packet = capture.verify_demo_account_records(
        v7_observations(selected, pages=pages),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
        barrier_completed_at=NOW - timedelta(seconds=2),
    )
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    reference = observed.CaptureReference(
        capture_id="a" * 32,
        head_sha256="b" * 64,
        plan_sha256=packet.plan_sha256,
        packet_sha256=frozen.sha256,
        session_binding_sha256=sha(selected.session_binding_id.encode("ascii")),
    )
    # _project is deliberately tested as a pure helper. The public observer
    # still requires an actual one-use native lease, which is not issued here.
    source = native._ObservedNativeDemoAccountRawPacket(
        packet=packet,
        reference=reference,
        receipt_sha256="c" * 64,
        proof_sha256="d" * 64,
        readback_sha256="e" * 64,
        observed_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=30),
    )
    return components._project(source), source


def test_v7_projection_accounts_for_all_eight_algo_streams_without_authority():
    result, _ = synthetic_projected_v7()
    receipt = json.loads(result.receipt_json)
    assert receipt["schema_version"] == "ctcc.native_demo_current_components.v2"
    assert receipt["policy_sha256"] == components.V7_POLICY_SHA256
    assert set(receipt["current_inventory_row_counts"]) == {
        "positions",
        "orders_pending",
        *(f"algo_{kind}" for kind in capture.CURRENT_ALGO_ORDER_TYPES_V7),
    }
    assert all(count == 0 for count in receipt["current_inventory_row_counts"].values())
    assert receipt["current_inventory_observed_empty"] is True
    assert receipt["history_complete"] is None
    assert receipt["local_exposure_complete"] is None
    assert receipt["active_protection_complete"] is None
    assert receipt["snapshot"] is result.snapshot is None
    assert receipt["account_complete"] is result.account_complete is False
    assert receipt["execution_authority"] is result.execution_authority is False
    assert receipt["admission"] == "DENY"


@pytest.mark.parametrize("kind", ["chase", "iceberg", "twap", "smart_iceberg"])
def test_v7_projected_new_algo_exposure_never_appears_flat(kind):
    stream = f"algo_{kind}"
    result, _ = synthetic_projected_v7(exposed_stream=stream)
    receipt = json.loads(result.receipt_json)
    assert receipt["current_inventory_row_counts"][stream] == 1
    assert receipt["current_inventory_observed_empty"] is False
    assert "current_exchange_exposure_present" in receipt["blocking_reasons"]
    assert receipt["account_complete"] is receipt["execution_authority"] is False


def test_v7_projection_rejects_packet_schema_relabel():
    _, source = synthetic_projected_v7()
    changed = native._ObservedNativeDemoAccountRawPacket(
        packet=source.packet.model_copy(
            update={"schema_version": "ctcc.demo_current_account_capture.v6"}
        ),
        reference=source.reference,
        receipt_sha256=source.receipt_sha256,
        proof_sha256=source.proof_sha256,
        readback_sha256=source.readback_sha256,
        observed_at=source.observed_at,
        expires_at=source.expires_at,
    )
    with pytest.raises(
        components.NativeCurrentComponentsError,
        match="native_current_components_source_invalid",
    ):
        components._project(changed)
