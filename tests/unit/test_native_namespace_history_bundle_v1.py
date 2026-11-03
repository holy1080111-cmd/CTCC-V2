"""Actual V6 raw/page replay over synthetic source; never native acceptance."""

import hashlib
import json
from copy import deepcopy

import pytest

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import native_namespace_history_bundle_v1 as bundle
from tests.unit import test_qualification_account_algo_history_v6 as original
from tests.unit import test_qualification_account_capture as old
from tests.unit.test_native_namespace_phase_succession_v1 import _series


def _packet(*, extra=None):
    selected = original.plan()
    observations = original.observations(selected, extra=extra)
    packet = capture.verify_demo_account_records(
        observations,
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
        barrier_completed_at=old.BARRIER,
    )
    return packet, selected, observations


def test_original_short_page_then_empty_preserves_raw_query_cursor_and_wire_order():
    stream = "orders_history_recent_swap"
    rows = [
        old.row("orders_history_recent", identifier=value) for value in ("1501", "1500")
    ]
    packet, _, _ = _packet(extra={stream: [rows, []]})
    inventory = bundle.namespace_queries(packet)
    assert len(inventory) == 52
    observed = next(item for item in inventory if item["stream"] == stream)
    actual = [page for page in packet.observations if page.request.stream == stream]
    assert observed["cursor_type"] == "ordId" and observed["row_count"] == 2
    assert len(observed["pages"]) == 2
    assert observed["pages"][0]["terminal"] is False
    assert observed["pages"][1]["terminal"] is True
    assert observed["pages"][1]["cursor_value"] == "1500"
    assert observed["pages"][1]["previous_page_sha256"] == actual[0].receipt_sha256
    assert (
        observed["pages"][0]["raw_sha256"]
        == hashlib.sha256(actual[0].response_body).hexdigest()
    )
    assert [row.row_id for row in actual[0].rows] == ["1501", "1500"]
    assert dict(observed["pages"][0]["request"]["parameters"])["instType"] == "SWAP"
    assert bundle.POLICY["namespace_finality"].startswith("UNKNOWN")
    assert all(bundle.POLICY[field] is False for field in bundle.FALSE_FIELDS)


@pytest.mark.parametrize(
    "damage",
    ["missing_terminal", "duplicate_cursor", "raw_changed", "missing_algo_partition"],
)
def test_actual_original_packet_replay_refuses_incomplete_or_malformed_chains(damage):
    packet, selected, observations = _packet()
    observed = list(observations)
    if damage == "missing_terminal":
        observed = [
            page
            for page in observed
            if page.request.stream != "orders_history_recent_swap"
        ]
    elif damage == "duplicate_cursor":
        first = next(
            page
            for page in observed
            if page.request.stream == original.STREAM and not page.terminal
        )
        index = observed.index(first)
        observed.insert(index + 1, first)
    elif damage == "raw_changed":
        index = next(
            i
            for i, page in enumerate(observed)
            if page.request.stream == original.STREAM
        )
        observed[index] = observed[index].model_copy(
            update={"response_body": b'{"code":"0","data":[malformed]}'}
        )
    else:
        observed = [
            page
            for page in observed
            if page.request.stream != "algo_history_twap_order_failed"
        ]
    assert bundle.namespace_queries(packet)
    with pytest.raises(capture.AccountCaptureError):
        capture.verify_demo_account_records(
            tuple(observed),
            plan=selected,
            expected_plan_sha256=capture.plan_sha256(selected),
            barrier_completed_at=old.BARRIER,
        )


def test_algo_history_query_never_invents_a_time_filter_or_terminal_partition():
    packet, _, _ = _packet()
    inventory = bundle.namespace_queries(packet)
    algorithms = [
        item for item in inventory if item["stream"].startswith("algo_history_")
    ]
    assert len(algorithms) == 24
    for query in algorithms:
        assert query["cursor_type"] == "algoId"
        assert query["time_domain"] == "no_begin_end_filter_documented"
        for page in query["pages"]:
            parameters = dict(page["request"]["parameters"])
            assert "begin" not in parameters and "end" not in parameters
    fills = next(item for item in inventory if item["stream"] == "fills_recent")
    assert fills["cursor_type"] == "billId"
    assert fills["time_domain"] == "record_generation_ts_not_fillTime"


@pytest.mark.parametrize(
    "claim",
    [
        "execution_authority",
        "g6_qualified",
        "namespace_finality_verified",
        "current_risk_verified",
    ],
)
def test_rehashed_caller_positive_flags_cannot_become_native_source_or_G6(claim):
    value = {
        "schema_version": bundle.SCHEMA,
        "policy_sha256": bundle.POLICY_SHA256,
        "phase": {},
        "route_proof": {},
        "chain": [],
        "temporal": {},
        "source_reference": {},
        "query_inventory": [],
        "query_receipt_hex": "00",
        "cutoff_ns": 1,
        "requested_start_ms": "1",
        "requested_end_ms": "1",
        "namespace_finality": "UNKNOWN",
        **{field: False for field in bundle.FALSE_FIELDS},
    }
    value[claim] = True
    raw = journal.canonical(value)
    with pytest.raises(bundle.NamespaceHistoryBundleError, match="bundle_contract"):
        bundle.replay_native_namespace_history_bundle_v1(
            raw, expected_sha256=hashlib.sha256(raw).hexdigest()
        )


def _saved_succession():
    previous, series = _series()
    hops = ()
    for state, observed_hop in series[1:]:
        hops = bundle.phase._join({**previous, "hops": hops}, state, observed_hop)
    saved = [
        {
            "active_records_json": hop["active"].records_json,
            "control_rows": list(hop["control_rows"]),
            "scan_json": hop["scan_json"].decode("ascii"),
            "scan_keys": sorted(hop["scan_keys"]),
            "source_plan_sha256": hop["source_plan_sha256"],
            "credential_session_sha256": hop["credential_session_sha256"],
            # This pure succession test never calls the V4 bundle replay or
            # claims native custody; that separate stage requires real bytes.
            "account_bundle_hex": "00",
        }
        for hop in hops
    ]
    historical = previous["historical"]
    scope = historical["active"].scope
    return historical, scope, saved


def test_actual_pure_coverage_control_succession_replays_two_expired_phase_windows():
    historical, scope, saved = _saved_succession()
    original_expiry = historical["active"].state_document["source_current_expires_at"]
    replayed = bundle._replay_succession(
        scope, historical["active"], historical["event_keys"], saved
    )
    assert len(replayed) == 2
    assert replayed[-1]["scan_keys"] == historical["event_keys"]
    assert all(type(row) is str for hop in saved for row in hop["control_rows"])
    assert (
        replayed[-1]["active"].state_document["source_current_expires_at"]
        != original_expiry
    )
    assert (
        historical["active"].state_document["source_current_expires_at"]
        == original_expiry
    )
    assert (
        bundle._control_lineage(saved[-1]["control_rows"], scope).execution_authority
        is False
    )


@pytest.mark.parametrize(
    "damage",
    [
        "control_prefix_truncated",
        "control_hash_chain",
        "control_unknown_action",
        "generation_gap",
        "revocation_anchor",
        "lost_tombstone",
        "scan_positive_claim",
        "plan_pin",
        "credential_pin",
    ],
)
def test_saved_lineage_mutations_cannot_keep_an_unchanged_cutover_seed(damage):
    historical, scope, saved = _saved_succession()
    # Baseline first: every negative must reach the actual successful replay
    # before rehashed saved inputs are damaged. No native owner is constructed.
    assert (
        len(
            bundle._replay_succession(
                scope, historical["active"], historical["event_keys"], saved
            )
        )
        == 2
    )
    damaged = deepcopy(saved)
    if damage == "control_prefix_truncated":
        damaged[-1]["control_rows"] = damaged[-1]["control_rows"][1:]
    elif damage in {"control_hash_chain", "control_unknown_action"}:
        event = json.loads(damaged[-1]["control_rows"][-1])
        event["previous_sha256" if damage == "control_hash_chain" else "action"] = (
            "0" * 64 if damage == "control_hash_chain" else "caller_complete"
        )
        damaged[-1]["control_rows"][-1] = bundle.control.canonical(event)
    elif damage == "generation_gap":
        damaged = [damaged[-1]]
    elif damage == "revocation_anchor":
        active = json.loads(damaged[-1]["active_records_json"])
        active["previous_terminal_anchor"]["previous_sha256"] = "0" * 64
        damaged[-1]["active_records_json"] = bundle.coverage_rows.canonical(active)
    elif damage == "lost_tombstone":
        damaged[-1]["scan_keys"] = []
        scan = json.loads(damaged[-1]["scan_json"])
        scan.update(event_count=0, event_keys_sha256=hashlib.sha256(b"[]").hexdigest())
        damaged[-1]["scan_json"] = journal.canonical(scan).decode("ascii")
    elif damage == "scan_positive_claim":
        scan = json.loads(damaged[-1]["scan_json"])
        scan["external_event_history_complete"] = True
        damaged[-1]["scan_json"] = journal.canonical(scan).decode("ascii")
    else:
        damaged[-1][
            "source_plan_sha256"
            if damage == "plan_pin"
            else "credential_session_sha256"
        ] = "0" * 64
    with pytest.raises(ValueError):
        bundle._replay_succession(
            scope, historical["active"], historical["event_keys"], damaged
        )


def test_phase_serializer_keeps_actual_control_event_json_strings(monkeypatch):
    historical, scope, saved = _saved_succession()
    hops = bundle._replay_succession(
        scope, historical["active"], historical["event_keys"], saved
    )
    from types import SimpleNamespace

    # Isolate serialization only. The replay seam below is explicitly synthetic;
    # neither this byte document nor replay DTO can reconstruct a native owner.
    monkeypatch.setattr(
        bundle.current,
        "replay_current_account_bundle_v4",
        lambda *args, **kwargs: SimpleNamespace(
            expires_at=old.BARRIER, monotonic_deadline_ns=1
        ),
    )
    original = SimpleNamespace(
        record_json=b"{}", record_sha256="1" * 64, recorded_at=old.BARRIER
    )
    moved = {
        "historical": {
            **historical,
            "record": original,
            "phase_source": {"bundle_raw": b"original"},
        },
        "current_account": {"bundle_raw": b"current", "bundle_sha256": "2" * 64},
        "active": hops[-1]["active"],
        "hops": hops,
        "phase_checked": {"utc_ns": 1, "monotonic_ns": 1},
    }
    document = bundle.phase_document(moved, source_bundles=(b"one", b"two"))
    assert document["original_active_records_json"] == historical["active"].records_json
    assert document["hops"][-1]["control_rows"] == saved[-1]["control_rows"]
    assert document["hops"][-1]["account_bundle_hex"] == b"two".hex()
