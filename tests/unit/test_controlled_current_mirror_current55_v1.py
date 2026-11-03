"""Inert DATA/storage invariants only: no account/native/SQL acceptance.

The private projection/value helpers cannot create a native source, Arm, claims,
reservation, intent or order authority. No database or private source is used.
"""

import copy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.database.repositories.qualification_ledger import PortfolioLocalCheckpoint
from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_verifier as verifier
from app.trade_qualification import controlled_current_mirror_current55_v1 as mirror
from app.trade_qualification.reservations import LedgerScope

AT = datetime(2026, 11, 2, tzinfo=UTC)


def _values():
    source = SimpleNamespace(
        scope=LedgerScope(account_id="123", settlement_currency="USDT"),
        read_session=SimpleNamespace(binding_id="a" * 64),
        current_custody_id="b" * 32,
        current_bundle_sha256="c" * 64,
    )
    counts = {name: 0 for name in verifier.V7_INVENTORY_STREAMS}
    receipt = {"observed_flat": True, "blocking_reasons": []}
    return source, counts, receipt


def _previous(**changes):
    values = {
        "details": {"existing_unknown_metadata": "retain-exactly"},
        "last_error": None,
        "status": "reconciled",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_inert_empty_data_projection_does_not_initialize_legacy_automation_or_financial_zero():
    source, counts, receipt = _values()
    actual = mirror._next_checkpoint_values(
        source, None, canonical({"rows": []}), counts, receipt, AT
    )
    assert actual["status"] == "reconciled"
    assert (
        actual["order_count"]
        == actual["position_count"]
        == actual["algo_order_count"]
        == 0
    )
    assert actual["details"]["account_uid_sha256"] == sha(b"123")
    assert actual["details"]["credential_session_binding_id"] == "a" * 64
    assert set(actual) == {
        "status",
        "order_count",
        "position_count",
        "algo_order_count",
        "details",
        "last_error",
        "reconciled_at",
        "updated_at",
    }
    assert (
        "armed" not in actual and "equity" not in actual and "loss_streak" not in actual
    )


@pytest.mark.parametrize("stream", tuple(verifier.V7_INVENTORY_STREAMS))
def test_every_original_exposure_stream_preserves_observed_nonflat_count(stream):
    source, counts, receipt = _values()
    counts[stream] = 1
    receipt.update(
        observed_flat=False,
        blocking_reasons=["current_exposure_requires_protection_and_local_join"],
    )
    actual = mirror._next_checkpoint_values(
        source, _previous(), b"{}", counts, receipt, AT
    )
    assert actual["status"] == "source_incomplete_or_exposed"
    assert actual["reconciled_at"] is None
    assert (
        actual["order_count"] + actual["position_count"] + actual["algo_order_count"]
        == 1
    )
    assert actual["details"]["existing_unknown_metadata"] == "retain-exactly"


@pytest.mark.parametrize(
    "error",
    [
        "okx_demo_order_write_requires_reconcile",
        "okx_demo_order_mirror_failure",
        "other_unresolved_error",
    ],
)
def test_new_complete_source_never_clears_existing_error(error):
    source, counts, receipt = _values()
    actual = mirror._next_checkpoint_values(
        source, _previous(last_error=error), b"{}", counts, receipt, AT
    )
    assert actual["last_error"] == error
    assert actual["status"] == "error"
    assert actual["reconciled_at"] is None


def test_error_status_without_message_is_not_promoted_to_reconciled():
    source, counts, receipt = _values()
    actual = mirror._next_checkpoint_values(
        source, _previous(status="error"), b"{}", counts, receipt, AT
    )
    assert actual["status"] == "error" and actual["last_error"] is None


def test_other_account_checkpoint_is_not_relabelled():
    source, counts, receipt = _values()
    previous = _previous(details={"account_uid_sha256": sha(b"456")})
    with pytest.raises(
        mirror.ControlledCurrentMirrorErrorV1, match="other_account_bound"
    ):
        mirror._next_checkpoint_values(source, previous, b"{}", counts, receipt, AT)
    assert previous.details == {"account_uid_sha256": sha(b"456")}


@pytest.mark.parametrize(
    "mutant", ["missing", "bool", "negative", "overflow", "flat_nonzero"]
)
def test_count_unknown_or_contradiction_is_not_zero(mutant):
    source, counts, receipt = _values()
    if mutant == "missing":
        del counts["positions"]
    elif mutant == "bool":
        counts["positions"] = False
    elif mutant == "negative":
        counts["positions"] = -1
    elif mutant == "overflow":
        counts["positions"] = 8193
    else:
        counts["positions"] = 1
    with pytest.raises(
        mirror.ControlledCurrentMirrorErrorV1, match="source_counts_invalid"
    ):
        mirror._next_checkpoint_values(source, None, b"{}", counts, receipt, AT)


def _local():
    return {
        "schema_version": "ctcc.portfolio_local_checkpoint.v2",
        "environment": "demo",
        "account_id": "123",
        "requested_settlement_currency": "USDT",
        "currency_states": [
            {"settlement_currency": "USDT", "active": ["hold1"], "claims_sha256": None}
        ],
        "unresolved_intents": [["intent1", "USDT", "consumed", 1, "unknown"]],
        "legacy": {
            "automation": {"armed": False, "emergency_stop": True, "active_trades": {}},
            "sync_checkpoint": None,
            "orders": ["untracked1"],
            "positions": ["position1"],
            "algos": ["protection1"],
        },
        "blocking_reasons": [
            "legacy_reconciliation_initialization_unknown",
            "all_currency_local_intents_unresolved",
        ],
        "future_writer_exclusion": False,
        "execution_authority": False,
    }


def _point(value):
    return PortfolioLocalCheckpoint(canonical(value), AT, AT)


def test_only_inventory_checkpoint_delta_is_permitted_not_local_authority_changes():
    before = _local()
    after = copy.deepcopy(before)
    after["legacy"]["sync_checkpoint"] = {"credential_session_binding_id": "a" * 64}
    after["blocking_reasons"].remove("legacy_reconciliation_initialization_unknown")
    mirror._same_other_local(_point(before), _point(after))
    assert after["unresolved_intents"] == before["unresolved_intents"]
    assert after["currency_states"] == before["currency_states"]


@pytest.mark.parametrize(
    "section",
    [
        "intent",
        "hold",
        "unknown_claims",
        "order",
        "position",
        "protection",
        "estop",
        "arm",
    ],
)
def test_full_local_snapshot_cannot_be_sanitized_for_flatness(section):
    before = _local()
    after = copy.deepcopy(before)
    if section == "intent":
        after["unresolved_intents"] = []
    elif section == "hold":
        after["currency_states"][0]["active"] = []
    elif section == "unknown_claims":
        after["currency_states"][0]["claims_sha256"] = "0" * 64
    elif section in {"order", "position", "protection"}:
        key = {"order": "orders", "position": "positions", "protection": "algos"}[
            section
        ]
        after["legacy"][key] = []
    else:
        after["legacy"]["automation"][
            "emergency_stop" if section == "estop" else "armed"
        ] = section == "arm"
    with pytest.raises(
        mirror.ControlledCurrentMirrorErrorV1, match="other_local_state_changed"
    ):
        mirror._same_other_local(_point(before), _point(after))


def test_unknown_retains_same_real_state_and_does_not_run_cleanup_or_retry(monkeypatch):
    active, unknown = {}, {}
    monkeypatch.setattr(mirror, "_ACTIVE", active)
    monkeypatch.setattr(mirror, "_UNKNOWN", unknown)
    token, source, session, transaction, coroutine = (object() for _ in range(5))
    state = {
        "operation_token": token,
        "phase": "ACTIVE",
        "source": source,
        "sessions": [session],
        "transactions": [transaction],
        "coroutine": coroutine,
    }
    active[token] = state
    mirror._retain_unknown(state)
    assert active == {} and unknown[token] is state
    assert state["source"] is source and state["sessions"][0] is session
    assert state["transactions"][0] is transaction and state["coroutine"] is coroutine
    mirror._retain_unknown(state)
    assert len(unknown) == 1 and unknown[token] is state


@pytest.mark.asyncio
async def test_combined_capacity_denies_before_source_replay_or_coroutine_creation(
    monkeypatch,
):
    monkeypatch.setattr(mirror, "_ACTIVE", {object(): {} for _ in range(8)})
    monkeypatch.setattr(mirror, "_UNKNOWN", {object(): {} for _ in range(8)})

    class Foreign:
        def __getattribute__(self, name):
            raise AssertionError("foreign callback invoked")

    assert len(mirror._ACTIVE) + len(mirror._UNKNOWN) == mirror._MAX_OPERATIONS
    with pytest.raises(
        mirror.ControlledCurrentMirrorErrorV1, match="operation_capacity"
    ):
        mirror.synchronize_owned_current_mirror_v1(Foreign())


def _inert_pages():
    # Typed model_construct values deliberately omit source ownership/proofs.
    # This directly tests the private pure DATA helper, never _projection or
    # synchronize_owned_current_mirror_v1 positive admission.
    pages = []
    for stream in verifier.V7_CURRENT_STREAMS:
        request = capture.AccountRequest.model_construct(stream=stream, parameters=())
        pages.append(
            capture.DemoAccountObservation.model_construct(
                request=request,
                page_index=0,
                after=None,
                previous_page_sha256=None,
                body_sha256="d" * 64,
                receipt_sha256="e" * 64,
                terminal=True,
                rows=(),
                request_started_at=AT,
                headers_received_at=AT,
                body_completed_at=AT,
            )
        )
    packet = capture.DemoAccountPacket.model_construct(observations=tuple(pages))
    receipt = {
        "inventory_row_counts": {stream: 0 for stream in verifier.V7_INVENTORY_STREAMS}
    }
    return packet, receipt


def test_page_data_preserves_original_unicode_json_hash_and_order():
    packet, receipt = _inert_pages()
    at = next(
        index
        for index, page in enumerate(packet.observations)
        if page.request.stream == "positions"
    )
    raw = '{"instId":"BTC-USDT-SWAP","note":"原始值"}'
    first = capture.AccountRow.model_construct(row_id="z", canonical_json=raw)
    second = capture.AccountRow.model_construct(
        row_id="a", canonical_json='{"pos":"1"}'
    )
    replaced = list(packet.observations)
    replaced[at] = replaced[at].model_copy(update={"rows": (first, second)})
    packet = packet.model_copy(update={"observations": tuple(replaced)})
    receipt["inventory_row_counts"]["positions"] = 2
    pages, counts = mirror._page_memberships(packet, receipt)
    assert [row["row_id"] for row in pages[at]["rows"]] == ["z", "a"]
    assert pages[at]["rows"][0]["original_row_json"] == raw
    assert pages[at]["rows"][0]["row_sha256"] == sha(raw.encode("utf-8"))
    assert counts["positions"] == 2


@pytest.mark.parametrize(
    "mutant",
    [
        "missing_stream",
        "nonterminal_empty",
        "count_missing",
        "count_bool",
        "count_conflict",
    ],
)
def test_short_or_empty_page_data_is_not_complete_without_all_original_terminal_chains(
    mutant,
):
    packet, receipt = _inert_pages()
    if mutant == "missing_stream":
        packet = packet.model_copy(update={"observations": packet.observations[:-1]})
    elif mutant == "nonterminal_empty":
        pages = (
            *packet.observations[:-1],
            packet.observations[-1].model_copy(update={"terminal": False}),
        )
        packet = packet.model_copy(update={"observations": pages})
    elif mutant == "count_missing":
        del receipt["inventory_row_counts"]["positions"]
    elif mutant == "count_bool":
        receipt["inventory_row_counts"]["positions"] = False
    else:
        receipt["inventory_row_counts"]["positions"] = 1
    with pytest.raises(
        mirror.ControlledCurrentMirrorErrorV1, match="complete_page_chain_required"
    ):
        mirror._page_memberships(packet, receipt)


def test_existing_empty_error_sentinel_is_preserved_as_no_error_without_changing_other_state():
    source, counts, receipt = _values()
    actual = mirror._next_checkpoint_values(
        source, _previous(last_error=""), b"{}", counts, receipt, AT
    )
    assert actual["last_error"] == "" and actual["status"] == "reconciled"
    assert actual["details"]["existing_unknown_metadata"] == "retain-exactly"


def test_retained_accounting_reads_actual_buffers_without_copy_or_lazy_orm_access():
    raw = b"actual source projection"
    equal_copy = bytes(bytearray(raw))
    assert equal_copy is not raw
    text = "original checkpoint JSON"
    before = _point(_local())
    checkpoint = mirror.OkxDemoSyncCheckpoint(
        id=1,
        status="error",
        details={"raw": text},
        order_count=0,
        position_count=0,
        algo_order_count=0,
    )
    audit = mirror.AuditLog(
        actor="test", action="test", resource_type="test", after={"raw": text}
    )
    state = {
        "source_projection_json": raw,
        "checkpoint_json": equal_copy,
        "write_receipt": text,
        "local_before": before,
        "checkpoint_row": checkpoint,
        "audit_row": audit,
        "written": {"audit_after": {"same_text": text}, "ledger_json": "ledger"},
    }
    actual = mirror._buffers_from_operation_v1(state)
    assert any(value is raw for value in actual)
    assert any(value is equal_copy for value in actual)
    assert sum(value is text for value in actual) == 1
    assert any(value is before.document_json for value in actual)
    assert all(type(value) in (bytes, str) for value in actual)


@pytest.mark.parametrize("mutant", ["foreign", "deep", "nonfinite", "bytes"])
def test_retained_accounting_is_bounded_and_never_invokes_foreign_values(mutant):
    class Foreign:
        def __getattribute__(self, name):
            raise AssertionError("foreign callback invoked")

    if mutant == "foreign":
        value = Foreign()
    elif mutant == "nonfinite":
        value = float("inf")
    elif mutant == "bytes":
        value = b"x" * (mirror._MAX_CHECKPOINT + 1)
    else:
        value = "leaf"
        for _ in range(34):
            value = [value]
    with pytest.raises(mirror.ControlledCurrentMirrorErrorV1):
        mirror._buffers_from_operation_v1(
            {"written": {"audit_after": {"value": value}}}
        )


def _control_value(*, stopped, armed):
    from datetime import timedelta

    from app.trade_qualification import demo_control as control

    return control.ControlState(
        scope=control.ControlScope("demo", "123"),
        revision=2,
        owner_sha256="a" * 64,
        owner_epoch=1,
        lease_until=AT + timedelta(seconds=30),
        pins=control.ControlPins("b" * 64, "c" * 64, "d" * 64),
        emergency_stop=stopped,
        arm_request_id="e" * 64 if armed else None,
        arm_expires_at=AT + timedelta(seconds=10) if armed else None,
        created_at=AT,
        updated_at=AT,
    )


def test_actual_stopped_operator_uses_original_owner_lease_without_inventing_arm_expiry():
    from datetime import timedelta

    value = _control_value(stopped=True, armed=False)
    assert mirror._mirror_control_deadline_v1(value, AT) == AT + timedelta(seconds=30)
    assert (
        value.emergency_stop is True
        and value.arm_request_id is None
        and value.arm_expires_at is None
    )
    assert value.execution_authority is False


def test_existing_armed_mirror_path_keeps_original_shorter_arm_expiry():
    from datetime import timedelta

    value = _control_value(stopped=False, armed=True)
    assert mirror._mirror_control_deadline_v1(value, AT) == AT + timedelta(seconds=10)
    with pytest.raises(mirror.ControlledCurrentMirrorErrorV1):
        mirror._mirror_control_deadline_v1(value, AT + timedelta(seconds=10))


def test_unstopped_disarmed_context_cannot_impersonate_operator_or_armed_source():
    with pytest.raises(mirror.ControlledCurrentMirrorErrorV1):
        mirror._mirror_control_deadline_v1(
            _control_value(stopped=False, armed=False), AT
        )


def test_stopped_owner_expiry_is_not_renewed():
    from datetime import timedelta

    value = _control_value(stopped=True, armed=False)
    with pytest.raises(
        mirror.ControlledCurrentMirrorErrorV1, match="stopped_operator_lease_required"
    ):
        mirror._mirror_control_deadline_v1(value, AT + timedelta(seconds=30))
