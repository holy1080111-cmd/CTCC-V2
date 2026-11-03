"""Prepared exact synthetic B1 wire negatives; no native/TLS/PG acceptance."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest

from app.database.repositories import account_history_original_frame_v1 as decoder
from app.domain import account_history_original_frame_v1 as policy
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_original_stream_v1 as stream
from app.trade_qualification import account_history_source_v1 as native
from app.trade_qualification import account_observation_index as observed
from tests.unit.test_account_history_original_source_catalog_v1 import (
    declared_source_catalog,
)
from tests.unit.test_account_history_original_stream_v1 import (
    SCOPE,
    original_chain,
    originals,
)
from tests.unit.test_qualification_account_capture import NOW

RECEIVED = NOW + timedelta(days=1)


def wire_for(frames, number):
    selected = frames[number - 1]
    document = json.loads(selected.batch.document_json)
    chains = []
    for sequence, chain in [
        (number, selected.chain),
        *((item.sequence, item.chain) for item in selected.witnesses),
    ]:
        original = frames[sequence - 1]
        original_document = json.loads(original.batch.document_json)
        chains.append(
            {
                "sequence": sequence,
                "event_sha256": original.batch.event_sha256,
                "reference": observed.reference_document(
                    observed.source_reference(chain)
                ),
                "source_sequence": len(chain),
                "source_membership_sha256": original_document[
                    "source_membership_sha256"
                ],
                "coverage_membership_sha256": original_document[
                    "coverage_membership_sha256"
                ],
                "rows": [
                    {
                        "event_hex": point.event.event_json.hex(),
                        "raw_hex": None
                        if point.event.raw_body is None
                        else point.event.raw_body.hex(),
                        "packet_hex": None
                        if point.event.packet_payload is None
                        else point.event.packet_payload.hex(),
                        "db_recorded_at": point.db_recorded_at.isoformat(),
                    }
                    for point in chain
                ],
            }
        )
    raw_bytes = sum(
        len(point.event.event_json)
        + len(point.event.raw_body or b"")
        + len(point.event.packet_payload or b"")
        for chain in [selected.chain, *(item.chain for item in selected.witnesses)]
        for point in chain
    )
    metadata = sum(
        len(raw)
        for raw in (
            selected.batch.document_json,
            selected.batch.replay.receipt_json,
            selected.native_registration,
            selected.checkpoint,
        )
        if raw is not None
    )
    events = sum(len(item["rows"]) for item in chains)
    wire_bound = (
        2 * raw_bytes
        + 6 * metadata
        + policy.ROW_WIRE_ALLOWANCE * events
        + policy.SOURCE_WIRE_ALLOWANCE * len(chains)
        + policy.HEADER_WIRE_ALLOWANCE
    )
    request = decoder.OriginalFrameRequestV1(
        SCOPE,
        len(frames),
        frames[-1].batch.event_sha256,
        number - 1,
        None if number == 1 else frames[number - 2].batch.event_sha256,
        3,
        0,
        0,
        RECEIVED + timedelta(seconds=5),
    )
    value = {
        "schema_version": policy.SCHEMA,
        "scope": [SCOPE.environment, SCOPE.account_id, SCOPE.settlement_currency],
        "through_sequence": len(frames),
        "head_sha256": request.head,
        "after_sequence": number - 1,
        "previous_sha256": request.previous,
        "next_after": number,
        "has_more": number < len(frames),
        "control_revision": 3,
        "account_revision": 0,
        "ledger_revision": 0,
        "backend_pid": 1234,
        "transaction_id": "123456",
        "db_observed_at": RECEIVED.isoformat(),
        "source_raw_bytes": raw_bytes,
        "metadata_utf8_bytes": metadata,
        "wire_upper_bound_bytes": wire_bound,
        "b3_recorded_at": selected.chain[-1].db_recorded_at.isoformat(),
        "document_hex": selected.batch.document_json.hex(),
        "receipt_hex": selected.batch.replay.receipt_json.hex(),
        "native_registration_hex": None
        if selected.native_registration is None
        else selected.native_registration.hex(),
        "checkpoint_hex": None
        if selected.checkpoint is None
        else selected.checkpoint.hex(),
        "chains": chains,
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    assert document["sequence"] == number
    return value, request


@pytest.fixture
async def original_wire(monkeypatch):
    frames = originals(await original_chain(monkeypatch), count=2, witness_at={2: (1,)})
    return frames, *wire_for(frames, 2)


async def test_original_bytes_and_recording_times_remain_exact_with_separate_receipt(
    original_wire,
):
    frames, value, request = original_wire
    result = decoder.decode_original_frame_wire_v1(
        value, request=request, client_received_at=RECEIVED
    )
    assert result.frame.batch == frames[-1].batch
    for old_chain, new_chain in (
        (frames[-1].chain, result.frame.chain),
        (frames[0].chain, result.frame.witnesses[0].chain),
    ):
        assert len(old_chain) == len(new_chain)
        assert [(point.event, point.db_recorded_at) for point in new_chain] == [
            (point.event, point.db_recorded_at) for point in old_chain
        ]
        assert all(point.readback_at == RECEIVED for point in new_chain)
    assert result.client_received_at == RECEIVED
    assert (
        result.account_complete
        is result.execution_authority
        is result.native_history_prefix_verified
        is False
    )
    with pytest.raises(native.NativeHistorySourceError):
        native.consume_owned_native_history_source_facts_v1(result)


@pytest.mark.parametrize(
    "change",
    [
        "scope",
        "next_bool",
        "control_bool",
        "has_more_int",
        "authority",
        "head",
        "predecessor",
        "source_length_bool",
        "missing_row",
        "reordered_rows",
        "wrong_bytes",
        "wrong_wire_budget",
        "uppercase_hex",
        "unicode_hex",
        "missing_witness",
        "forward_witness",
        "witness_hash",
        "future_db_time",
        "future_recorded",
        "extra_field",
        "missing_packet",
        "wrong_event_bytes",
    ],
)
async def test_changed_wire_has_no_partial_observation(original_wire, change):
    _, original, request = original_wire
    value = deepcopy(original)
    if change == "scope":
        value["scope"][0] = "live"
    elif change == "next_bool":
        value["next_after"] = True
    elif change == "control_bool":
        value["control_revision"] = True
    elif change == "has_more_int":
        value["has_more"] = 0
    elif change == "authority":
        value["execution_authority"] = True
    elif change == "head":
        value["head_sha256"] = "f" * 64
    elif change == "predecessor":
        value["previous_sha256"] = "f" * 64
    elif change == "source_length_bool":
        value["chains"][0]["source_sequence"] = True
    elif change == "missing_row":
        value["chains"][0]["rows"].pop()
    elif change == "reordered_rows":
        value["chains"][0]["rows"].reverse()
    elif change == "wrong_bytes":
        value["source_raw_bytes"] += 1
    elif change == "wrong_wire_budget":
        value["wire_upper_bound_bytes"] -= 1
    elif change == "uppercase_hex":
        value["document_hex"] = value["document_hex"].upper()
    elif change == "unicode_hex":
        value["document_hex"] = "é" * len(value["document_hex"])
    elif change == "missing_witness":
        value["chains"].pop()
    elif change == "forward_witness":
        value["chains"][1]["sequence"] = 2
    elif change == "witness_hash":
        value["chains"][1]["event_sha256"] = "f" * 64
    elif change == "future_db_time":
        value["db_observed_at"] = (RECEIVED + timedelta(seconds=1)).isoformat()
    elif change == "future_recorded":
        value["chains"][0]["rows"][0]["db_recorded_at"] = (
            RECEIVED + timedelta(seconds=1)
        ).isoformat()
    elif change == "extra_field":
        value["passed"] = True
    elif change == "missing_packet":
        point = next(
            row for row in value["chains"][0]["rows"] if row["packet_hex"] is not None
        )
        point["packet_hex"] = None
    else:
        value["chains"][0]["rows"][0]["event_hex"] = "7b7d"
    with pytest.raises(decoder.OriginalFrameWireError):
        decoder.decode_original_frame_wire_v1(
            value, request=request, client_received_at=RECEIVED
        )


@pytest.mark.parametrize("bound", ["MAX_RAW_FRAME_BYTES", "MAX_WIRE_BYTES"])
async def test_budget_is_checked_before_any_binary_decode(
    original_wire, monkeypatch, bound
):
    _, value, request = original_wire
    called = []
    monkeypatch.setattr(decoder, "_unhex", lambda *args, **kwargs: called.append(True))
    monkeypatch.setattr(policy, bound, 1)
    with pytest.raises(decoder.OriginalFrameWireError, match="budget"):
        decoder.decode_original_frame_wire_v1(
            value, request=request, client_received_at=RECEIVED
        )
    assert called == []


async def test_original_lease_expiry_is_not_repaired_by_a_new_receipt(original_wire):
    _, value, request = original_wire
    with pytest.raises(decoder.OriginalFrameWireError, match="original_lease_expired"):
        decoder.decode_original_frame_wire_v1(
            value, request=request, client_received_at=request.expires_at
        )


async def test_full_original32_then33_uses_width1_pages_and_remains_inert(monkeypatch):
    frames = originals(
        await original_chain(monkeypatch), count=33, witness_at={33: (1,)}
    )

    async def producer():
        previous = None
        for number in range(1, 34):
            value, request = wire_for(frames, number)
            decoded = decoder.decode_original_frame_wire_v1(
                value, request=request, client_received_at=RECEIVED
            )
            page = decoder.original_frame_width1_page_v1(
                decoded, page_index=number - 1, previous_page_sha256=previous
            )
            previous = stream.original_history_page_sha256_v1(page)
            yield page

    result = await stream.replay_original_history_stream_async_v1(
        producer(),
        scope=SCOPE,
        expected_through_sequence=33,
        expected_b3_head_sha256=frames[-1].batch.event_sha256,
        expected_checkpoint_sha256=journal.digest(frames[31].checkpoint),
    )
    receipt = json.loads(result.receipt_json)
    assert receipt["through_sequence"] == 33
    assert receipt["original_partition_count"] == 1
    assert receipt["active_partition_member_count"] == 1
    assert receipt["page_count"] == 33
    assert receipt["admission"] == "DENY"
    assert receipt["account_complete"] is receipt["execution_authority"] is False


def declared_catalog(*, collector=True):
    owner, reader = 20000, 20001
    role = reader if collector else owner
    previous = {
        "tables": [{"owner_oid": owner, "namespace_oid": 2200}],
        "role": {
            "oid": role,
            "name": "declared_collector",
            "can_login": True,
            **{
                key: False
                for key in (
                    "superuser",
                    "createdb",
                    "createrole",
                    "replication",
                    "bypassrls",
                    "other_membership",
                    "can_create",
                )
            },
        },
        "server": {
            "current_user": "declared_collector",
            "session_user": "declared_collector",
            "role_setting": "none",
            "timezone": "UTC",
            "search_path": "pg_catalog, public, pg_temp",
            "isolation": "read committed",
            "replication_role": "origin",
            "synchronous_commit": "on",
            "fsync": "on",
            "full_page_writes": "on",
            "in_recovery": False,
        },
    }
    source_catalog, source_previous = declared_source_catalog()
    previous["parents"] = source_previous["parents"]
    value = {
        "source_table_access": not collector,
        "schema_usage": True,
        "source_catalog": source_catalog,
        "functions": [
            {
                "name": policy.FUNCTION,
                "oid": 30000,
                "namespace": "public",
                "namespace_oid": 2200,
                "owner_oid": owner,
                "arguments": policy.SIGNATURE,
                "identity_arguments": policy.ARGUMENTS.replace(",", ", "),
                "returns": "jsonb",
                "language": "plpgsql",
                "source": policy.BODY,
                "definition": "synthetic deparser output, never a native seal",
                "security_definer": True,
                "volatility": "v",
                "config": ["search_path=pg_catalog, public, pg_temp"],
                "kind": "f",
                "parallel": "u",
                "strict": False,
                "leakproof": False,
                "can_execute": True,
                "acl": [
                    {
                        "grantee_oid": oid,
                        "grantor_oid": owner,
                        "privilege": "EXECUTE",
                        "grantable": False,
                    }
                    for oid in ([owner, reader] if collector else [owner])
                ],
            }
        ],
    }
    return value, previous


def test_installation_grants_no_old_reader_and_synthetic_catalog_never_seals_runtime():
    from app.domain import account_history_partition_catalog_v1 as parent

    owner_only, previous = declared_catalog(collector=False)
    assert (
        policy.verify_catalog(owner_only, previous, require_native_baseline=False)
        == policy.DDL_SHA256
    )
    granted, previous = declared_catalog()
    with pytest.raises(parent.AccountHistoryPartitionError, match="acl_drift"):
        policy.verify_catalog(granted, previous, require_native_baseline=False)
    assert (
        policy.verify_catalog(
            granted,
            previous,
            approved_collector_role_oid=20001,
            require_native_baseline=False,
        )
        == policy.DDL_SHA256
    )
    with pytest.raises(
        parent.AccountHistoryPartitionError,
        match="^original_frame_source_deparser_drift$",
    ):
        policy.verify_catalog(granted, previous, approved_collector_role_oid=20001)


@pytest.mark.parametrize(
    "change",
    [
        "public",
        "unknown_role",
        "grant_option",
        "extra_overload",
        "source",
        "signature",
        "owner",
        "direct_table_access",
    ],
)
def test_new_raw_read_capability_requires_exact_explicit_role_and_catalog(change):
    from app.domain import account_history_partition_catalog_v1 as parent

    value, previous = declared_catalog()
    row = value["functions"][0]
    if change == "public":
        row["acl"][1]["grantee_oid"] = 0
    elif change == "unknown_role":
        row["acl"][1]["grantee_oid"] = 20002
    elif change == "grant_option":
        row["acl"][1]["grantable"] = True
    elif change == "extra_overload":
        value["functions"].append(deepcopy(row))
    elif change == "source":
        row["source"] = "BEGIN RETURN '{}'::jsonb; END"
    elif change == "signature":
        row["arguments"] = "text"
    elif change == "owner":
        row["owner_oid"] = 20001
    else:
        value["source_table_access"] = True
    with pytest.raises(parent.AccountHistoryPartitionError):
        policy.verify_catalog(value, previous, approved_collector_role_oid=20001)


def test_descriptor_validation_is_at_each_retention_site_and_before_raw_export():
    # Source-order regression complements the stored-source PostgreSQL cases.
    # It is not a substitute for running those real SQL cases.
    assert policy.BODY.count("sources:=array_append(sources,descriptor);") == 2
    assert policy.BODY.count("octet_length(descriptor::text)>4096") == 2
    assert policy.BODY.count("original_frame_source_membership") == 2
    assert policy.BODY.index(
        "original_source_catalog:=source_catalog"
    ) < policy.BODY.index("SELECT * INTO head_row")
    assert policy.BODY.index("witness_row.source_sequence") < policy.BODY.index(
        "-- PHASE1"
    )
    assert policy.BODY.index("-- PHASE1") < policy.BODY.index(
        "encode(convert_to(e.event_json"
    )
    assert "source_catalog IS DISTINCT FROM original_source_catalog" in policy.BODY


def test_reviewed_native_seals_preserve_frozen_install_contracts():
    from app.domain import account_history_original_source_catalog_v1 as source_guard
    from app.domain import account_history_partition_catalog_v1 as parent

    assert source_guard.PG17_NATIVE_BASELINE_SHA256 == (
        "5a20251836bfbbf0523d050e7eb475f0a7a74b39b1ba5532130b76dcf6e3c780"
    )
    assert policy.PG17_NATIVE_BASELINE_SHA256 == (
        "0743e54edd02450dc0d9e4744715ba25fa59872a086f81ef95d3cff1fdfb4343"
    )
    assert (
        source_guard.PG17_NATIVE_BASELINE_STATUS
        == (policy.PG17_NATIVE_BASELINE_STATUS)
        == "SEALED_FROM_REVIEWED_NATIVE_PG17"
    )
    assert source_guard.CONTRACT_SHA256 == (
        "790d159d0cbbce8346f7431f91f8d6fe2fe3ef3d8fcc05c5c322090d154d77a3"
    )
    assert policy.DDL_SHA256 == (
        "abe0ca020c2e6ffc5fd551b043c7d8db02812920cd5f0146c7881fab70d08dbb"
    )
    assert parent.PG17_DEPARSED_BASELINE == (
        "37190935d1b8e2807d26590225165779bb12e9684c260dd19fb61fba0d3c64c3"
    )
