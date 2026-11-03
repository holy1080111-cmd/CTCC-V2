"""Pure exact original-frame decoder; native SQL acquisition remains unwired.

Neither the request DTO, decoded observation nor stream page is a source owner.
The future native producer must retain original factory/clock/Control owners
and check every RPC and session-close await. No direct B1 SELECT is provided.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from app.database.repositories.account_observation_index import ObservationBatchReadback
from app.domain import account_history_original_frame_v1 as policy
from app.domain import account_history_partition_v1 as records
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_original_stream_v1 as stream
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope

SQL = (
    "SELECT public.account_history_original_frame_v1_read(:environment,:account_id,"
    ":currency,:through,:head,:after,:previous,:control,:account,:ledger,:expires)"
)
_HEX = re.compile(r"[0-9a-f]+\Z", re.ASCII)


class OriginalFrameWireError(ValueError):
    """Fixed source-safe errors; no original payload or database error text."""


def _deny(code):
    raise OriginalFrameWireError(code) from None


def _keys(value, expected):
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != expected
    ):
        _deny("original_frame_wire_shape")


def _utc(value):
    if type(value) is not datetime or value.tzinfo is not UTC:
        _deny("original_frame_exact_utc_required")
    return value


def _wire_time(value, received_at):
    if type(value) is not str or not 20 <= len(value) <= 40:
        _deny("original_frame_recorded_time_invalid")
    at = _utc(datetime.fromisoformat(value))
    if at > received_at:
        _deny("original_frame_recorded_time_future")
    return at


@dataclass(frozen=True, slots=True, repr=False)
class OriginalFrameRequestV1:
    scope: records.HistoryPartitionScopeV1
    through: int
    head: str
    after: int
    previous: str | None
    control: int
    account: int
    ledger: int
    expires_at: datetime

    def __post_init__(self):
        records._scope(self.scope)
        for name, zero in (
            ("through", False),
            ("after", True),
            ("control", False),
            ("account", True),
            ("ledger", True),
        ):
            records._number(getattr(self, name), zero=zero)
        records._hash(self.head)
        if self.after >= self.through:
            _deny("original_frame_request_cursor")
        if self.after == 0:
            if self.previous is not None:
                _deny("original_frame_request_cursor")
        else:
            records._hash(self.previous)
        _utc(self.expires_at)


@dataclass(frozen=True, slots=True, repr=False)
class DecodedOriginalFrameV1:
    frame: stream.OriginalHistoryFrameV1
    db_observed_at: datetime
    client_received_at: datetime
    backend_pid: int
    transaction_id: str
    source_raw_bytes: int
    metadata_utf8_bytes: int
    wire_upper_bound_bytes: int

    @property
    def account_complete(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def native_history_prefix_verified(self):
        return False


def _hex_size(value, maximum, *, optional=False):
    if value is None and optional:
        return 0
    if type(value) is not str or not 0 < len(value) <= maximum * 2 or len(value) % 2:
        _deny("original_frame_hex_bound_or_type")
    if not value.isascii() or _HEX.fullmatch(value) is None:
        _deny("original_frame_hex_noncanonical")
    return len(value) // 2


def _unhex(value, *, optional=False):
    if value is None and optional:
        return None
    if _HEX.fullmatch(value) is None:
        _deny("original_frame_hex_noncanonical")
    raw = bytes.fromhex(value)
    if raw.hex() != value:
        _deny("original_frame_hex_noncanonical")
    return raw


def _preflight(value, request, received_at):
    keys = {
        "schema_version",
        "scope",
        "through_sequence",
        "head_sha256",
        "after_sequence",
        "previous_sha256",
        "next_after",
        "has_more",
        "control_revision",
        "account_revision",
        "ledger_revision",
        "backend_pid",
        "transaction_id",
        "db_observed_at",
        "source_raw_bytes",
        "metadata_utf8_bytes",
        "wire_upper_bound_bytes",
        "b3_recorded_at",
        "document_hex",
        "receipt_hex",
        "native_registration_hex",
        "checkpoint_hex",
        "chains",
        "account_complete",
        "execution_authority",
        "admission",
    }
    _keys(value, keys)
    if (
        type(value["schema_version"]) is not str
        or value["schema_version"] != policy.SCHEMA
    ):
        _deny("original_frame_wire_shape")
    if (
        type(value["scope"]) is not list
        or len(value["scope"]) != 3
        or any(type(item) is not str for item in value["scope"])
        or value["scope"] != records._scope(request.scope)
    ):
        _deny("original_frame_scope_changed")
    for wire_name, expected, zero in (
        ("through_sequence", request.through, False),
        ("after_sequence", request.after, True),
        ("next_after", request.after + 1, False),
        ("control_revision", request.control, False),
        ("account_revision", request.account, True),
        ("ledger_revision", request.ledger, True),
    ):
        records._number(value[wire_name], zero=zero)
        if value[wire_name] != expected:
            _deny("original_frame_request_pin_changed")
    if (
        type(value["head_sha256"]) is not str
        or value["head_sha256"] != request.head
        or (
            value["previous_sha256"] is not None
            and type(value["previous_sha256"]) is not str
        )
        or value["previous_sha256"] != request.previous
        or type(value["has_more"]) is not bool
        or value["has_more"] is not (request.after + 1 < request.through)
        or value["account_complete"] is not False
        or value["execution_authority"] is not False
        or type(value["admission"]) is not str
        or value["admission"] != "DENY"
        or type(value["backend_pid"]) is not int
        or not 0 < value["backend_pid"] < 2**31
        or type(value["transaction_id"]) is not str
        or not 1 <= len(value["transaction_id"]) <= 20
        or not value["transaction_id"].isascii()
        or not value["transaction_id"].isdigit()
    ):
        _deny("original_frame_request_or_authority_invalid")
    if received_at >= request.expires_at:
        _deny("original_frame_original_lease_expired")
    observed_at = _wire_time(value["db_observed_at"], received_at)
    _wire_time(value["b3_recorded_at"], observed_at)
    metadata = sum(
        _hex_size(value[name], maximum, optional=optional)
        for name, maximum, optional in (
            ("document_hex", 262144, False),
            ("receipt_hex", observed.MAX_RECEIPT_BYTES, False),
            ("native_registration_hex", records.MAX_RECORD_BYTES, True),
            ("checkpoint_hex", records.MAX_RECORD_BYTES, True),
        )
    )
    chains = value["chains"]
    if type(chains) is not list or not 1 <= len(chains) <= policy.MAX_WITNESSES + 1:
        _deny("original_frame_source_count_bound")
    raw_bytes = event_count = 0
    prior = 0
    chain_keys = {
        "sequence",
        "event_sha256",
        "reference",
        "source_sequence",
        "source_membership_sha256",
        "coverage_membership_sha256",
        "rows",
    }
    for offset, chain in enumerate(chains):
        _keys(chain, chain_keys)
        records._number(chain["sequence"])
        if offset == 0:
            if chain["sequence"] != request.after + 1:
                _deny("original_frame_current_source_changed")
        elif not prior < chain["sequence"] < request.after + 1:
            _deny("original_frame_witness_order")
        else:
            prior = chain["sequence"]
        for key in (
            "event_sha256",
            "source_membership_sha256",
            "coverage_membership_sha256",
        ):
            records._hash(chain[key])
        _keys(
            chain["reference"],
            {
                "capture_id",
                "head_sha256",
                "plan_sha256",
                "packet_sha256",
                "session_binding_sha256",
            },
        )
        reference = observed.CaptureReference(**chain["reference"])
        if observed.reference_document(reference) != chain["reference"]:
            _deny("original_frame_reference_shape")
        rows = chain["rows"]
        if (
            type(chain["source_sequence"]) is not int
            or not 1 <= chain["source_sequence"] <= journal.MAX_EVENTS
            or type(rows) is not list
            or len(rows) != chain["source_sequence"]
        ):
            _deny("original_frame_chain_count_or_gap")
        event_count += len(rows)
        for row in rows:
            _keys(row, {"event_hex", "raw_hex", "packet_hex", "db_recorded_at"})
            raw_bytes += _hex_size(row["event_hex"], 262144)
            raw_bytes += _hex_size(row["raw_hex"], journal.PAGE_BYTES, optional=True)
            raw_bytes += _hex_size(
                row["packet_hex"], capture.MAX_PACKET_BYTES, optional=True
            )
            _wire_time(row["db_recorded_at"], observed_at)
            if raw_bytes + metadata > policy.MAX_RAW_FRAME_BYTES:
                _deny("original_frame_raw_budget")
    wire_bound = (
        2 * raw_bytes
        + 6 * metadata
        + policy.ROW_WIRE_ALLOWANCE * event_count
        + policy.SOURCE_WIRE_ALLOWANCE * len(chains)
        + policy.HEADER_WIRE_ALLOWANCE
    )
    if wire_bound > policy.MAX_WIRE_BYTES:
        _deny("original_frame_wire_budget")
    for key, actual in (
        ("source_raw_bytes", raw_bytes),
        ("metadata_utf8_bytes", metadata),
        ("wire_upper_bound_bytes", wire_bound),
    ):
        if type(value[key]) is not int or value[key] != actual:
            _deny("original_frame_byte_accounting_changed")
    # All field shapes/lengths have been bounded before this full encoding or
    # any bytes.fromhex call. This is a wire cap, not a Python RSS guarantee.
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")
    if len(encoded) > policy.MAX_WIRE_BYTES or len(encoded) > wire_bound:
        _deny("original_frame_wire_budget")
    return observed_at, raw_bytes, metadata, wire_bound


def _decode_chain(value, scope, received_at):
    rows, previous, prior_recorded = [], None, None
    for number, row in enumerate(value["rows"], 1):
        event = journal._JournalEvent(
            journal._ISSUER,
            _unhex(row["event_hex"]),
            _unhex(row["raw_hex"], optional=True),
            _unhex(row["packet_hex"], optional=True),
        )
        data = journal.checked_event(event)
        recorded = _wire_time(row["db_recorded_at"], received_at)
        if (
            data["sequence"] != number
            or data["previous_sha256"] != previous
            or (prior_recorded is not None and recorded < prior_recorded)
        ):
            _deny("original_frame_original_chain_changed")
        # The new client receipt is measured separately; original event/source
        # timestamps and original DB recording timestamps remain untouched.
        rows.append(journal.JournalReadback(event, recorded, received_at))
        previous, prior_recorded = journal.digest(event.event_json), recorded
    chain = tuple(rows)
    reference, _, _, _ = proof._source(chain, scope)
    if observed.reference_document(reference) != value["reference"]:
        _deny("original_frame_original_chain_reference_changed")
    return chain


def decode_original_frame_wire_v1(value, *, request, client_received_at):
    """Reopen exact original bytes as an inert observation; never mint owner."""
    try:
        if type(request) is not OriginalFrameRequestV1:
            _deny("original_frame_exact_request_required")
        request.__post_init__()
        received = _utc(client_received_at)
        observed_at, raw_bytes, metadata, wire_bound = _preflight(
            value, request, received
        )
        from app.trade_qualification import account_original_page_memo_v1 as page_memo

        page_memo._bind_validated_rpc(
            observed_at, value["backend_pid"], value["transaction_id"]
        )

        raw_document = _unhex(value["document_hex"])
        raw_receipt = _unhex(value["receipt_hex"])
        document, _ = records._b3(
            raw_document,
            scope=records._scope(request.scope),
            source_policy=observed.POLICY_SHA256,
        )
        receipt = records._json(raw_receipt)
        current = value["chains"][0]
        if (
            document["sequence"] != request.after + 1
            or document["previous_sha256"] != request.previous
            or records.digest(raw_document) != current["event_sha256"]
            or document["receipt_sha256"] != records.digest(raw_receipt)
            or document["reference"] != current["reference"]
            or document["source_membership_sha256"]
            != current["source_membership_sha256"]
            or document["coverage_membership_sha256"]
            != current["coverage_membership_sha256"]
            or journal.canonical(receipt.get("continuation_anchor")).decode("ascii")
            != document["anchor_json"]
            or receipt.get("scope_sha256") != document["scope_sha256"]
            or receipt.get("account_complete") is not False
            or receipt.get("execution_authority") is not False
            or receipt.get("admission") != "DENY"
        ):
            _deny("original_frame_original_b3_changed")
        witness_items = receipt["continuation_anchor"]["verified_witnesses"]
        if (
            type(witness_items) is not list
            or len(witness_items) != len(value["chains"]) - 1
        ):
            _deny("original_frame_original_witnesses_changed")
        for witness, original in zip(value["chains"][1:], witness_items, strict=True):
            expected = {
                name: witness[name]
                for name in (
                    "sequence",
                    "event_sha256",
                    "source_membership_sha256",
                    "coverage_membership_sha256",
                )
            }
            if journal.canonical(original) != journal.canonical(expected):
                _deny("original_frame_original_witnesses_changed")
        ledger_scope = LedgerScope(
            account_id=request.scope.account_id,
            settlement_currency=request.scope.settlement_currency,
        )
        chains = tuple(
            _decode_chain(item, ledger_scope, received) for item in value["chains"]
        )
        if (
            _wire_time(value["b3_recorded_at"], observed_at)
            < chains[0][-1].db_recorded_at
        ):
            _deny("original_frame_b3_precedes_original_source")
        native_registration = _unhex(value["native_registration_hex"], optional=True)
        checkpoint = _unhex(value["checkpoint_hex"], optional=True)
        if native_registration is not None:
            member = records._json(native_registration)
            derived = records.derive_native_member_registration_v1(
                scope=request.scope,
                b3_document_raw=raw_document,
                native_linkage_raw=records._ascii(member["native_linkage_json"]),
                expected_b3_policy_sha256=observed.POLICY_SHA256,
                expected_b3_event_sha256=current["event_sha256"],
            )
            if derived.record_json != native_registration:
                _deny("original_frame_native_registration_changed")
        if checkpoint is not None:
            item = records._json(checkpoint)
            if (
                document["sequence"] % records.PARTITION_SPAN
                or item["scope"] != records._scope(request.scope)
                or item["last_sequence"] != document["sequence"]
                or item["terminal_event_sha256"] != current["event_sha256"]
            ):
                _deny("original_frame_checkpoint_changed")
        frame = stream.OriginalHistoryFrameV1(
            chains[0],
            ObservationBatchReadback(
                document["sequence"],
                current["event_sha256"],
                raw_document,
                observed.ObservationReplay(raw_receipt),
            ),
            tuple(
                stream.OriginalHistoryWitnessV1(item["sequence"], chain)
                for item, chain in zip(value["chains"][1:], chains[1:], strict=True)
            ),
            native_registration,
            checkpoint,
        )
        measured, _ = stream._frame_measure(frame)
        if measured != raw_bytes + metadata:
            _deny("original_frame_decoded_byte_count_changed")
        if (
            document["sequence"] == request.through
            and current["event_sha256"] != request.head
        ):
            _deny("original_frame_final_head_changed")
        return DecodedOriginalFrameV1(
            frame,
            observed_at,
            received,
            value["backend_pid"],
            value["transaction_id"],
            raw_bytes,
            metadata,
            wire_bound,
        )
    except OriginalFrameWireError:
        raise
    except Exception:  # noqa: BLE001 -- no private rows/SQL exception text
        _deny("original_frame_wire_invalid")


def original_frame_width1_page_v1(decoded, *, page_index, previous_page_sha256):
    if (
        type(decoded) is not DecodedOriginalFrameV1
        or type(page_index) is not int
        or page_index < 0
    ):
        _deny("original_frame_exact_observation_required")
    page = stream.OriginalHistoryPageV1(
        page_index, previous_page_sha256, (decoded.frame,)
    )
    stream.original_history_page_sha256_v1(page)
    return page
