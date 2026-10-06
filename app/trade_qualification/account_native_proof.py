"""Pure replay of original account/native companions. Replay issues no owner."""

import re
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from urllib.parse import urlsplit

from app.domain import native_clock as public_clock
from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_history_query_verifier as history
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_time_probe import (
    AccountTimeProbePlanV1,
    AccountTimeProbeReceiptV1,
    verify_account_time,
)
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

SCHEMA = "ctcc.demo_account_native_clock_proof.v2"
V3_SCHEMA = "ctcc.demo_account_native_clock_proof.v3"
MAX_PROOF = 2 * 1024 * 1024
FILES = (
    "host-before.json",
    "exchange-plan.json",
    "exchange-before.json",
    "exchange-before.raw",
    "host-after.json",
    "exchange-after.json",
    "exchange-after.raw",
    "exchange-before-trace.json",
    "exchange-after-trace.json",
)
POLICY_BYTES = canonical(
    {
        "schema_version": "ctcc.demo_account_native_clock_policy.v2",
        "stage": "initial_account_only",
        "clock_profile": public_clock.PROFILE_ID,
        "clock_resources": list(public_clock.RESOURCE_IDS),
        "utc_conversion": "reviewed_integer_ns_round_up_to_microsecond",
        "maximum_current_age_ns": 30_000_000_000,
        "source_phases": [*native.PAGE_PHASES, "source_closed"],
        "exchange_origin": "https://openapi.okx.com",
        "exchange_endpoint": "/api/v5/public/time",
        "exchange_plan_schema": "ctcc.demo_account_exchange_time_plan.v1",
        "exchange_receipt_schema": "ctcc.demo_account_exchange_time_receipt.v1",
        "exchange_trace_schema": "ctcc.demo_account_exchange_time_trace.v2",
        "account_current_policy_sha256": current.POLICY_SHA256,
        "history_query_policy_sha256": history.POLICY_SHA256,
        "historical_hwm_clock_verified": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)
V3_POLICY_BYTES = canonical(
    {
        "schema_version": "ctcc.demo_account_native_clock_policy.v3",
        "prior_policy_sha256": POLICY_SHA256,
        "stage": "initial_account_only",
        "clock_profile": public_clock.PROFILE_ID,
        "clock_resources": list(public_clock.RESOURCE_IDS),
        "utc_conversion": "reviewed_integer_ns_round_up_to_microsecond",
        "maximum_current_age_ns": 30_000_000_000,
        "source_phases": [*native.PAGE_PHASES, "source_closed"],
        "exchange_origin": "https://openapi.okx.com",
        "exchange_endpoint": "/api/v5/public/time",
        "exchange_plan_schema": "ctcc.demo_account_exchange_time_plan.v1",
        "exchange_receipt_schema": "ctcc.demo_account_exchange_time_receipt.v1",
        "exchange_trace_schema": "ctcc.demo_account_exchange_time_trace.v2",
        "current_capture_plan": "ctcc.demo_current_account_plan.v6",
        "account_current_policy_sha256": current.V6_POLICY_SHA256,
        "history_query_policy_sha256": None,
        "separate_original_history_join_required": True,
        "historical_hwm_clock_verified": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
V3_POLICY_SHA256 = sha(V3_POLICY_BYTES)


def contract_for_plan(plan):
    """Select only the exact immutable source contract, never an old relabel."""
    if type(plan) is capture.AllProductDemoAccountCapturePlan:
        return SCHEMA, POLICY_SHA256
    if type(plan) is capture.CurrentDemoAccountCapturePlanV6:
        return V3_SCHEMA, V3_POLICY_SHA256
    raise NativeAccountProofError("native_account_source_scope_unsupported")


class NativeAccountProofError(ValueError):
    """Static private-data-safe errors."""


@dataclass(frozen=True, slots=True, repr=False)
class NativeAccountProofReplay:
    proof_sha256: str
    packet: object
    current_source_receipt_json: bytes
    expires_at: datetime
    monotonic_deadline_ns: int

    @property
    def execution_authority(self):
        return False

    @property
    def account_complete(self):
        return False


def _canonical_document(raw, maximum=MAX_PROOF):
    value = decode(raw, maximum)
    if canonical(value) != raw:
        raise NativeAccountProofError("native_account_proof_noncanonical")
    return value


def _exact_contract(value, expected):
    """Reject coercion and bool/int aliases before canonical content joins."""
    if type(value) is not type(expected):
        return False
    if type(expected) is dict:
        return (
            all(type(key) is str for key in value)
            and set(value) == set(expected)
            and all(_exact_contract(value[key], expected[key]) for key in expected)
        )
    if type(expected) is list:
        return len(value) == len(expected) and all(
            _exact_contract(part, template)
            for part, template in zip(value, expected, strict=True)
        )
    return type(expected) in (str, int, bool, type(None))


def _stamp(value):
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != {"utc_ns", "monotonic_ns"}
        or any(type(part) is not int for part in value.values())
    ):
        raise NativeAccountProofError("native_account_exact_clock_stamp_required")
    validate_stamps((value, value))
    return value


def scope_sha256(scope):
    checked_bootstrap(scope, LedgerScope)
    return journal.digest(
        journal.canonical(
            [
                scope.environment,
                scope.account_id,
                scope.settlement_currency,
            ]
        )
    )


def _source(chain, scope, *, proof_schema=SCHEMA):
    checked_bootstrap(scope, LedgerScope)
    if (
        type(chain) is not tuple
        or not chain
        or any(type(point) is not journal.JournalReadback for point in chain)
    ):
        raise NativeAccountProofError("native_account_exact_source_chain_required")
    # Revalidate the whole chain before reading any later packet-bearing event.
    for point in chain:
        journal.JournalReadback(point.event, point.db_recorded_at, point.readback_at)
    reference = observed.source_reference(chain)
    if proof_schema == SCHEMA:
        history.verify_history_query_chain(chain, **observed._pins(reference, scope))
        expected_plan = capture.AllProductDemoAccountCapturePlan
    elif proof_schema == V3_SCHEMA:
        current.verify_current_account_sources(
            chain,
            reference=reference,
            scope=scope,
            validated_at=datetime.fromisoformat(
                journal.checked_event(chain[-1].event)["observed_at"]
            ),
            expected_policy_sha256=current.V6_POLICY_SHA256,
        )
        expected_plan = capture.CurrentDemoAccountCapturePlanV6
    else:
        raise NativeAccountProofError("native_account_proof_contract_invalid")
    records = tuple(journal.checked_event(point.event) for point in chain)
    packets = tuple(
        point.event.packet_payload for point in chain if point.event.packet_payload
    )
    packet = capture.verify_demo_account_packet(
        packets[0],
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )
    if (
        type(packet.plan) is not expected_plan
        or packet.plan.registration_region != "global"
        or packet.plan.origin != "https://openapi.okx.com"
        or scope.environment != "demo"
        or scope.settlement_currency != "USDT"
    ):
        raise NativeAccountProofError("native_account_source_scope_unsupported")
    joins = []
    phase_kinds = (
        "request_start",
        "headers_received",
        "body_complete",
        "page_validated",
        "raw_finalized",
    )
    for record in records:
        if record["kind"] in phase_kinds and (
            type(record["data"].get("request_index")) is not int
            or not 0 <= record["data"]["request_index"] < len(packet.observations)
        ):
            raise NativeAccountProofError("native_account_original_index_invalid")
    for index, page in enumerate(packet.observations):
        matching = {
            kind: [
                record
                for record in records
                if record["kind"] == kind
                and record["data"].get("request_index") == index
            ]
            for kind in phase_kinds
        }
        if any(len(items) != 1 for items in matching.values()):
            raise NativeAccountProofError("native_account_original_phase_missing")
        transport = matching["raw_finalized"][0]["data"]
        peer = transport.get("tls_certificate_sha256")
        if transport.get("tls_provenance") != "owned_signed_verified_tls":
            raise NativeAccountProofError("native_account_original_tls_required")
        journal._sha(peer)
        hostname = transport.get("tls_hostname")
        if hostname is not None and (
            type(hostname) is not str
            or hostname != urlsplit(page.request.origin).hostname
        ):
            raise NativeAccountProofError(
                "native_account_original_tls_hostname_invalid"
            )
        joins.append(
            {
                "request_index": index,
                "stream": page.request.stream,
                "page_index": page.page_index,
                "request_sha256": journal.digest(
                    journal.canonical(capture._json_value(page.request))
                ),
                "raw_sha256": journal.digest(page.response_body),
                "page_receipt_sha256": page.receipt_sha256,
                "tls_peer_sha256": peer,
                **({"tls_hostname": hostname} if hostname is not None else {}),
                "original_event_sha256": {
                    kind: journal.digest(journal.canonical(items[0]))
                    for kind, items in matching.items()
                },
                "body_exhausted_at": matching["body_complete"][0]["observed_at"],
            }
        )
    return reference, packet, records, joins


def _time_trace(raw, *, phase, stage, scope_sha, plan, receipt, body):
    value = _canonical_document(raw, 512 * 1024)
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema_version",
            "phase",
            "plan_sha256",
            "invocation_sha256",
            "scope_sha256",
            "events",
            "observed_bytes",
            "retained_bytes",
            "raw_sha256",
            "raw_retention",
            "probe_receipt_sha256",
            "complete",
            "execution_authority",
        }
        or value["schema_version"] != "ctcc.demo_account_exchange_time_trace.v2"
        or value["phase"] != phase
        or value["plan_sha256"] != plan.canonical_sha256()
        or value["invocation_sha256"] != sha(canonical([stage["invocation_id"], phase]))
        or value["scope_sha256"] != scope_sha
        or value["complete"] is not True
        or value["execution_authority"] is not False
        or type(value["observed_bytes"]) is not int
        or value["observed_bytes"] != len(body)
        or type(value["retained_bytes"]) is not int
        or value["retained_bytes"] != len(body)
        or value["raw_sha256"] != sha(body)
        or value["raw_retention"] != "durable_secret_checked"
        or value["probe_receipt_sha256"] != receipt.canonical_sha256()
    ):
        raise NativeAccountProofError("native_account_time_trace_binding_invalid")
    events = value["events"]
    if type(events) is not list or not 7 <= len(events) <= 500:
        raise NativeAccountProofError("native_account_time_trace_inventory_invalid")
    kinds = [event.get("kind") for event in events]
    if (
        kinds[:2] != ["request_start", "headers_received"]
        or kinds[-4:]
        != ["body_complete", "validation_complete", "response_closed", "client_closed"]
        or any(kind != "chunk" for kind in kinds[2:-4])
    ):
        raise NativeAccountProofError("native_account_time_trace_inventory_invalid")
    stamps, previous, offset = [], None, 0
    for index, event in enumerate(events):
        if (
            type(event) is not dict
            or set(event)
            != {"index", "kind", "request_index", "stamp", "data", "previous_sha256"}
            or type(event["index"]) is not int
            or event["index"] != index
            or type(event["request_index"]) is not int
            or event["request_index"] != 0
            or event["previous_sha256"] != previous
            or type(event["data"]) is not dict
            or event["stamp"] is None
        ):
            raise NativeAccountProofError("native_account_time_trace_inventory_invalid")
        data, kind = event["data"], event["kind"]
        _stamp(event["stamp"])
        if kind == "request_start":
            expected = {"endpoint": "/api/v5/public/time", "query": []}
            matched_stamp = receipt.request_start
        elif kind == "headers_received":
            expected = {
                "status": 200,
                "safe_headers": [list(pair) for pair in receipt.response_headers],
                "tls": {
                    "classification": "owned_native_tls",
                    "peer_sha256": receipt.tls_peer_sha256,
                    "hostname": receipt.tls_hostname,
                    "version": receipt.tls_version,
                },
                "truncated": False,
            }
            matched_stamp = receipt.headers_received
        elif kind == "body_complete":
            expected = {"body_bytes": len(body), "body_sha256": sha(body)}
            matched_stamp = receipt.body_complete
        elif kind == "validation_complete":
            expected, matched_stamp = {}, receipt.validation_complete
        elif kind == "response_closed":
            expected, matched_stamp = (
                {"accepted": True, "cleanup": "closed", "error_code": "none"},
                None,
            )
        elif kind == "client_closed":
            expected, matched_stamp = {"successful": True}, None
        else:
            if (
                set(data)
                != {
                    "chunk_index",
                    "observed_bytes",
                    "observed_sha256",
                    "retained_bytes",
                    "retained_sha256",
                    "truncated",
                }
                or type(data["retained_bytes"]) is not int
                or not 0 <= data["retained_bytes"] <= len(body) - offset
            ):
                raise NativeAccountProofError("native_account_time_trace_chunk_invalid")
            kept = body[offset : offset + data["retained_bytes"]]
            expected = {
                "chunk_index": index - 2,
                "observed_bytes": len(kept),
                "observed_sha256": sha(kept),
                "retained_bytes": len(kept),
                "retained_sha256": sha(kept),
                "truncated": False,
            }
            matched_stamp = None
            offset += len(kept)
        # Canonical comparisons retain exact int/bool semantics in metadata.
        if (
            not _exact_contract(data, expected)
            or canonical(data) != canonical(expected)
            or (
                matched_stamp is not None
                and canonical(event["stamp"]) != canonical(matched_stamp)
            )
        ):
            raise NativeAccountProofError(
                "native_account_time_trace_phase_join_invalid"
            )
        stamps.append(event["stamp"])
        previous = sha(canonical(event))
    if offset != len(body):
        raise NativeAccountProofError("native_account_time_trace_chunk_missing")
    validate_stamps(stamps)
    return stamps


def _clocks(files, *, stage, scope_sha):
    if (
        type(files) is not dict
        or set(files) != set(FILES)
        or any(type(raw) is not bytes for raw in files.values())
    ):
        raise NativeAccountProofError("native_account_clock_inventory_invalid")
    hosts = [
        public_clock.replay_clock_observation(files[f"host-{phase}.json"])
        for phase in ("before", "after")
    ]
    if any(host["outcome"] != "accepted" for host in hosts):
        raise NativeAccountProofError("native_account_host_clock_rejected")
    observations = [host["observation"] for host in hosts]
    if any(
        host["schema_version"] != "ctcc.windows_clock_observation.v2"
        for host in observations
    ):
        raise NativeAccountProofError("native_account_clock_profile_invalid")
    if public_clock.clock_timezone(observations[0]) != public_clock.clock_timezone(
        observations[1]
    ):
        raise NativeAccountProofError("native_account_clock_domain_changed")
    plan_raw = files["exchange-plan.json"]
    plan = AccountTimeProbePlanV1.model_validate_json(plan_raw)
    if plan.canonical_bytes() != plan_raw or plan.origin != "https://openapi.okx.com":
        raise NativeAccountProofError("native_account_exchange_plan_invalid")
    probes, servers, traces = [], [], []
    for phase in ("before", "after"):
        receipt_raw = files[f"exchange-{phase}.json"]
        receipt = AccountTimeProbeReceiptV1.model_validate_json(receipt_raw)
        if (
            receipt.canonical_bytes() != receipt_raw
            or receipt.endpoint != plan.endpoint
            or receipt.transport_origin != "owned_native_tls"
        ):
            raise NativeAccountProofError("native_account_exchange_probe_invalid")
        servers.append(
            verify_account_time(plan, receipt, files[f"exchange-{phase}.raw"])
        )
        probes.append(receipt)
        traces.append(
            _time_trace(
                files[f"exchange-{phase}-trace.json"],
                phase=phase,
                stage=stage,
                scope_sha=scope_sha,
                plan=plan,
                receipt=receipt,
                body=files[f"exchange-{phase}.raw"],
            )
        )
    if servers[1] < servers[0]:
        raise NativeAccountProofError("native_account_exchange_time_reversed")
    return observations, probes, traces


def _replay(raw, files, chain, scope, expected):
    checked_bootstrap(scope, LedgerScope)
    if (
        type(files) is not dict
        or any(type(name) is not str for name in files)
        or set(files) != set(FILES)
        or any(type(body) is not bytes for body in files.values())
    ):
        raise NativeAccountProofError("native_account_clock_inventory_invalid")
    value = _canonical_document(raw)
    if type(expected) is not str or sha(raw) != expected:
        raise NativeAccountProofError("native_account_proof_pin_mismatch")
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema_version",
            "policy_sha256",
            "scope_sha256",
            "source_reference",
            "journal_terminal_sequence",
            "journal_owner_sha256",
            "local_checkpoint_sha256",
            "stage",
            "witnesses",
            "source_joins",
            "clock_file_sha256",
            "finalization_witnesses",
            "source_readback_complete",
            "proof_persist_start",
            "expires_at",
            "monotonic_deadline_ns",
            "historical_hwm_clock_verified",
            "account_complete",
            "execution_authority",
            "admission",
        }
        or value["schema_version"] not in {SCHEMA, V3_SCHEMA}
        or value["policy_sha256"]
        != (POLICY_SHA256 if value["schema_version"] == SCHEMA else V3_POLICY_SHA256)
    ):
        raise NativeAccountProofError("native_account_proof_contract_invalid")
    if (
        any(
            value[name] is not False
            for name in (
                "historical_hwm_clock_verified",
                "account_complete",
                "execution_authority",
            )
        )
        or value["admission"] != "DENY"
    ):
        raise NativeAccountProofError("native_account_proof_authority_forbidden")
    proof_schema = value["schema_version"]
    reference, packet, records, joins = _source(chain, scope, proof_schema=proof_schema)
    reference_value = observed.reference_document(reference)
    if (
        value["scope_sha256"] != scope_sha256(scope)
        or not _exact_contract(value["source_reference"], reference_value)
        or canonical(value["source_reference"]) != canonical(reference_value)
        or type(value["journal_terminal_sequence"]) is not int
        or value["journal_terminal_sequence"] != len(records)
        or value["journal_owner_sha256"]
        != records[0]["data"]["invocation_owner_sha256"]
        or value["local_checkpoint_sha256"]
        != records[0]["data"]["local_checkpoint_sha256"]
        or not _exact_contract(value["source_joins"], joins)
        or canonical(value["source_joins"]) != canonical(joins)
        or value["clock_file_sha256"] != {name: sha(files[name]) for name in FILES}
    ):
        raise NativeAccountProofError("native_account_source_binding_invalid")
    stage = value["stage"]
    if (
        type(stage) is not dict
        or set(stage) != {"kind", "invocation_id", "started"}
        or stage["kind"] != "initial_account_only"
        or type(stage["invocation_id"]) is not str
        or re.fullmatch(r"[a-f0-9]{32}", stage["invocation_id"]) is None
    ):
        raise NativeAccountProofError("native_account_initial_stage_invalid")
    _stamp(stage["started"])
    if packet.barrier_completed_at != utc_from_ns(stage["started"]["utc_ns"]):
        raise NativeAccountProofError("native_account_initial_stage_invalid")
    hosts, _probes, traces = _clocks(
        files, stage=stage, scope_sha=value["scope_sha256"]
    )
    witnesses = value["witnesses"]
    if (
        type(witnesses) is not list
        or len(witnesses) != len(joins) * len(native.PAGE_PHASES) + 1
    ):
        raise NativeAccountProofError("native_account_phase_inventory_invalid")
    previous, stamps = (
        None,
        [
            stage["started"],
            hosts[0]["diagnostic"]["host_before"]["request_start"],
            hosts[0]["sample"],
            *traces[0],
        ],
    )
    for index, witness in enumerate(witnesses):
        if type(witness) is not dict or set(witness) != {
            "index",
            "phase",
            "request_index",
            "stream",
            "page_index",
            "stamp",
            "source_utc",
            "previous_sha256",
        }:
            raise NativeAccountProofError("native_account_phase_contract_invalid")
        request_index, phase_index = divmod(index, len(native.PAGE_PHASES))
        source_close = index == len(witnesses) - 1
        page = None if source_close else packet.observations[request_index]
        phase = "source_closed" if source_close else native.PAGE_PHASES[phase_index]
        _stamp(witness["stamp"])
        if (
            type(witness["index"]) is not int
            or witness["index"] != index
            or type(witness["request_index"]) is not int
            or witness["request_index"] != request_index
            or type(witness["page_index"]) is not int
            or witness["phase"] != phase
            or witness["previous_sha256"] != previous
            or witness["stream"]
            != ("account_source" if source_close else page.request.stream)
            or witness["page_index"] != (0 if source_close else page.page_index)
            or witness["source_utc"]
            != utc_from_ns(witness["stamp"]["utc_ns"]).isoformat()
        ):
            raise NativeAccountProofError("native_account_phase_binding_invalid")
        expected_utc = (
            None
            if source_close or phase == "request_dispatch"
            else {
                "request_start": page.request_started_at.isoformat(),
                "headers_received": page.headers_received_at.isoformat(),
                "body_exhausted": joins[request_index]["body_exhausted_at"],
                "response_closed": page.body_completed_at.isoformat(),
            }[phase]
        )
        if expected_utc is not None and witness["source_utc"] != expected_utc:
            raise NativeAccountProofError("native_account_original_utc_changed")
        if phase in ("request_start", "request_dispatch") and (
            witness["stamp"]["utc_ns"] <= stage["started"]["utc_ns"]
            or witness["stamp"]["monotonic_ns"] <= stage["started"]["monotonic_ns"]
        ):
            raise NativeAccountProofError("native_account_initial_barrier_not_crossed")
        previous = sha(canonical(witness))
        stamps.append(witness["stamp"])
    finalizers = value["finalization_witnesses"]
    if type(finalizers) is not list or len(finalizers) != 2:
        raise NativeAccountProofError("native_account_finalization_inventory_invalid")
    for finalizer, purpose in zip(finalizers, ("source", "journal"), strict=True):
        if (
            type(finalizer) is not dict
            or set(finalizer)
            != {
                "kind",
                "finalizer_id",
                "invocation_binding_sha256",
                "started",
                "completed",
            }
            or finalizer["kind"] != f"original_b1_bounded_{purpose}_finalization"
            or type(finalizer["finalizer_id"]) is not str
            or re.fullmatch(r"[a-f0-9]{32}", finalizer["finalizer_id"]) is None
            or finalizer["invocation_binding_sha256"]
            != sha(canonical([stage["invocation_id"], finalizer["finalizer_id"]]))
        ):
            raise NativeAccountProofError(
                "native_account_finalization_inventory_invalid"
            )
        _stamp(finalizer["started"])
        _stamp(finalizer["completed"])
    if finalizers[0]["finalizer_id"] == finalizers[1]["finalizer_id"]:
        raise NativeAccountProofError("native_account_finalization_inventory_invalid")
    terminal = datetime.fromisoformat(records[-1]["observed_at"])
    if (
        not utc_from_ns(finalizers[1]["started"]["utc_ns"])
        <= terminal
        <= utc_from_ns(finalizers[1]["completed"]["utc_ns"])
    ):
        raise NativeAccountProofError("native_account_finalization_clock_invalid")
    stamps.extend(
        [
            finalizers[0]["started"],
            finalizers[0]["completed"],
            finalizers[1]["started"],
            finalizers[1]["completed"],
            hosts[1]["diagnostic"]["host_before"]["request_start"],
            hosts[1]["sample"],
            *traces[1],
            value["source_readback_complete"],
            value["proof_persist_start"],
        ]
    )
    _stamp(value["source_readback_complete"])
    _stamp(value["proof_persist_start"])
    # Each sample is compared against the same original anchor. This preserves
    # the existing 5ms/60s bounds even for >512 page witnesses; no relaxed policy.
    for prior, stamp in pairwise(stamps):
        validate_stamps((stamps[0], prior, stamp))
    first_current = next(
        w["stamp"]
        for w in witnesses
        if w["phase"] == "response_closed" and w["stream"] in current.CURRENT_STREAMS
    )
    expiry = capture._utc(datetime.fromisoformat(value["expires_at"]))
    deadline = value["monotonic_deadline_ns"]
    if (
        type(deadline) is not int
        or deadline != first_current["monotonic_ns"] + 30_000_000_000
        or expiry != utc_from_ns(first_current["utc_ns"] + 30_000_000_000)
        or value["proof_persist_start"]["monotonic_ns"] >= deadline
        or utc_from_ns(value["proof_persist_start"]["utc_ns"]) >= expiry
    ):
        raise NativeAccountProofError("native_account_original_expiry_invalid")
    verified = current.verify_current_account_sources(
        chain,
        reference=reference,
        scope=scope,
        validated_at=utc_from_ns(value["proof_persist_start"]["utc_ns"]),
        **(
            {}
            if proof_schema == SCHEMA
            else {"expected_policy_sha256": current.V6_POLICY_SHA256}
        ),
    )
    current_data = decode(verified.receipt_json)
    if current_data["blocking_reasons"] or current_data["observed_flat"] is not True:
        raise NativeAccountProofError(
            "native_account_current_sources_incomplete_or_exposed"
        )
    return NativeAccountProofReplay(
        expected, packet, verified.receipt_json, expiry, deadline
    )


def replay_native_account_proof(raw, *, files, chain, scope, expected_proof_sha256):
    try:
        return _replay(raw, files, chain, scope, expected_proof_sha256)
    except NativeAccountProofError:
        raise
    except Exception:  # noqa: BLE001 -- no original private data in errors
        raise NativeAccountProofError("native_account_proof_replay_invalid") from None


def _exchange_plan(started):
    # Exact unauthenticated public-time probe; no market-data coordinates.
    return AccountTimeProbePlanV1(
        created_ns=started["utc_ns"],
    )
