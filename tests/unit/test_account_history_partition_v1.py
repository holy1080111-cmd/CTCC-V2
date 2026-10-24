"""Synthetic original B3/linkage wire shapes; no native, DB or TLS acceptance."""

import json

import pytest

from app.domain import account_history_partition_v1 as partition

# Declared synthetic32-digit UID, never obtained from an exchange account.
SCOPE = partition.HistoryPartitionScopeV1(
    "demo", "99000000000000000000000000000001", "USDT"
)
SCOPE_DOC = ["demo", "99000000000000000000000000000001", "USDT"]


def pin(label):
    return partition.digest(partition.canonical(["declared_synthetic", str(label)]))


SOURCE_POLICY = pin("B3_original_policy")


def original_documents(count=64, *, duplicate_capture_at=None):
    """Build the actual existing B3 v1 document/anchor shape, not a native owner."""
    result = []
    previous_event = previous_anchor = None
    findings = 0
    for number in range(1, count + 1):
        finding_count = int(number == 7)
        findings += finding_count
        source_hash, coverage_hash = pin((number, "facts")), pin((number, "coverage"))
        anchor = {
            "schema_version": "ctcc.account_observation_continuation.v1",
            "policy_sha256": SOURCE_POLICY,
            "scope_sha256": partition.digest(partition.canonical(SCOPE_DOC)),
            "through_sequence": number,
            "previous_sequence": number - 1,
            "previous_event_sha256": previous_event,
            "previous_anchor_sha256": previous_anchor,
            "source_facts_through_sequence": number * 2,
            "coverage_through_sequence": number,
            "unresolved_findings_through_sequence": findings,
            "current_source_membership_sha256": source_hash,
            "current_coverage_membership_sha256": coverage_hash,
            "verified_witnesses": [],
            "required_proof_budget_exceeded": False,
        }
        anchor_raw = partition.canonical(anchor)
        reference = {
            "capture_id": f"{1 if number == duplicate_capture_at else number:032x}",
            "head_sha256": pin((number, "B1terminal")),
            "plan_sha256": pin((number, "plan")),
            "packet_sha256": pin((number, "packet")),
            "session_binding_sha256": pin((number, "session")),
        }
        document = {
            "schema_version": "ctcc.account_observation_batch.v1",
            "scope_sha256": anchor["scope_sha256"],
            "sequence": number,
            "previous_sha256": previous_event,
            "reference": reference,
            "window": {
                "started_at": "2026-01-01T00:00:00+00:00",
                "ended_at": "2026-01-02T00:00:00+00:00",
            },
            "policy_sha256": SOURCE_POLICY,
            "receipt_sha256": pin((number, "replay")),
            "anchor_json": anchor_raw.decode(),
            "anchor_sha256": partition.digest(anchor_raw),
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
            "source_membership_sha256": source_hash,
            "source_membership_count": 2,
            "coverage_membership_sha256": coverage_hash,
            "coverage_membership_count": 1,
            "finding_membership_sha256": pin((number, "finding")),
            "finding_membership_count": finding_count,
        }
        raw = partition.canonical(document)
        result.append(raw)
        previous_event, previous_anchor = (
            partition.digest(raw),
            document["anchor_sha256"],
        )
    return tuple(result)


def original_linkage(raw):
    document = json.loads(raw)
    number = document["sequence"]
    bundle = pin((number, "source.bundle.json"))
    # Lists preserve the original native issuer's [size, SHA256] inventory wire.
    return {
        "schema_version": partition.LINKAGE_SCHEMA,
        "scope": SCOPE_DOC,
        "b3_sequence": number,
        "b3_event_sha256": partition.digest(raw),
        "b3_anchor_sha256": document["anchor_sha256"],
        "latest_source_reference": document["reference"],
        "native_invocation_id": f"{10000 + number:032x}",
        "custody_bundle_id": f"{20000 + number:032x}",
        "bundle_sha256": bundle,
        "proof_sha256": pin((number, "proof")),
        "native_policy_sha256": pin("native_policy"),
        "source_manifest_sha256": pin("source_manifest"),
        "raw_inventory": {
            "source.bundle.json": [100, bundle],
            "accepted.json": [80, pin((number, "accepted"))],
            "attempt.claim.json": [70, pin((number, "claim"))],
        },
        "companion_inventory": {
            "original-clock.json": [80, pin((number, "clock-file"))]
        },
        "raw_root": {
            "path": f"C:/declared-synthetic/history-{number}",
            "device": 7,
            "inode": number * 2,
        },
        "companion_root": {
            "path": f"C:/declared-synthetic/companion-{number}",
            "device": 7,
            "inode": number * 2 + 1,
        },
        "original_started": {"utc_ns": 1000 + number, "monotonic_ns": 2000 + number},
        "native_started": {"utc_ns": 1100 + number, "monotonic_ns": 2100 + number},
        "source_readback_complete": {
            "utc_ns": 1200 + number,
            "monotonic_ns": 2200 + number,
        },
    }


def registration(raw, linkage=None):
    return partition.derive_native_member_registration_v1(
        scope=SCOPE,
        b3_document_raw=raw,
        native_linkage_raw=partition.canonical(
            original_linkage(raw) if linkage is None else linkage
        ),
        expected_b3_policy_sha256=SOURCE_POLICY,
        expected_b3_event_sha256=partition.digest(raw),
    ).record_json


def checkpoint(documents, *, previous=(), registrations=None):
    if registrations is None:
        registrations = tuple(registration(raw) for raw in documents)
    return partition.build_partition_checkpoint_v1(
        scope=SCOPE,
        b3_documents=documents,
        native_registrations=registrations,
        previous_checkpoints=previous,
        expected_b3_policy_sha256=SOURCE_POLICY,
        expected_b3_head_sha256=partition.digest(documents[-1]),
    )


def test_latest_member_registration_retains_original_linkage_and_unknowns():
    raw = original_documents(1)[0]
    value = json.loads(registration(raw))
    assert value["source_reference"] == json.loads(raw)["reference"]
    assert json.loads(value["native_linkage_json"]) == original_linkage(raw)
    assert (
        value["account_revision"] is None
        and value["source_authenticity_verified"] is False
    )
    for name in (
        "funding_accrual",
        "account_history_finality",
        "current_history_tail",
        "loss_streak_seed",
        "historical_native_hwm",
    ):
        assert value[name] is None
    for name in (
        "execution_authority",
        "account_complete",
        "clear_stop_permission",
        "arm_permission",
    ):
        assert value[name] is False


@pytest.mark.parametrize(
    "field", ["capture_id", "session_binding_sha256", "head_sha256"]
)
def test_bundle_containing_an_older_member_cannot_register_it_as_latest(field):
    one, two = original_documents(2)
    linkage = original_linkage(two)
    linkage["latest_source_reference"][field] = json.loads(one)["reference"][field]
    with pytest.raises(
        partition.HistoryPartitionError, match="partition_latest_native_member_mismatch"
    ):
        registration(two, linkage)


def test_original_clock_regression_and_inventory_aliases_are_refused():
    raw = original_documents(1)[0]
    linkage = original_linkage(raw)
    linkage["source_readback_complete"]["monotonic_ns"] = 1
    with pytest.raises(
        partition.HistoryPartitionError, match="partition_original_clock_regressed"
    ):
        registration(raw, linkage)
    linkage = original_linkage(raw)
    linkage["raw_inventory"]["ACCEPTED.json"] = linkage["raw_inventory"][
        "accepted.json"
    ]
    with pytest.raises(
        partition.HistoryPartitionError, match="partition_original_locator_invalid"
    ):
        registration(raw, linkage)
    linkage = original_linkage(raw)
    linkage["companion_root"] = dict(linkage["raw_root"])
    with pytest.raises(
        partition.HistoryPartitionError,
        match="partition_original_distinct_roots_required",
    ):
        registration(raw, linkage)


@pytest.mark.parametrize(
    "locator", [".", "../escape", "source//bundle", "C:/outside", "accepted.JSON"]
)
def test_original_inventory_cannot_use_ambiguous_or_aliased_locators(locator):
    raw = original_documents(1)[0]
    linkage = original_linkage(raw)
    linkage["raw_inventory"][locator] = [1, pin("ambiguous_locator")]
    with pytest.raises(
        partition.HistoryPartitionError, match="partition_original_locator_invalid"
    ):
        registration(raw, linkage)


def test_full32_checkpoint_is_deterministic_and_keeps_missing_registration_and_findings():
    documents = original_documents(32)
    registrations = tuple(
        None if i == 9 else registration(raw) for i, raw in enumerate(documents, 1)
    )
    result = checkpoint(documents, registrations=registrations)
    value = json.loads(result.record_json)
    assert (
        result.record_json
        == checkpoint(documents, registrations=registrations).record_json
    )
    assert value["missing_native_registration_sequences"] == [9]
    assert value["missing_native_registrations_through_sequence"] == 1
    assert value["native_registration_count"] == 31
    assert value["cumulative_counts"]["unresolved_findings_through_sequence"] == 1
    assert value["first_sequence"] == 1 and value["last_sequence"] == 32
    assert (
        tuple(member["b3_document_json"].encode() for member in value["members"])
        == documents
    )
    replay = partition.replay_partition_checkpoints_v1(
        scope=SCOPE,
        checkpoints=(result.record_json,),
        expected_checkpoint_sha256=result.record_sha256,
        expected_b3_policy_sha256=SOURCE_POLICY,
        expected_b3_head_sha256=partition.digest(documents[-1]),
    )
    assert replay.record_json == result.record_json


def test_next_partition_preserves_global_sequence_genesis_and_missing_predecessor_obligation():
    documents = original_documents()
    first = checkpoint(documents[:32], registrations=(None,) * 32)
    second = checkpoint(documents[32:], previous=(first.record_json,))
    value = json.loads(second.record_json)
    assert (
        value["first_sequence"],
        value["last_sequence"],
        value["partition_number"],
    ) == (33, 64, 2)
    assert value["previous_checkpoint_sha256"] == first.record_sha256
    assert value["genesis_event_sha256"] == partition.digest(documents[0])
    assert value["missing_native_registrations_through_sequence"] == 32
    with pytest.raises(
        partition.HistoryPartitionError, match="partition_global_chain_gap_or_changed"
    ):
        partition.replay_partition_checkpoints_v1(
            scope=SCOPE,
            checkpoints=(second.record_json,),
            expected_checkpoint_sha256=second.record_sha256,
            expected_b3_policy_sha256=SOURCE_POLICY,
            expected_b3_head_sha256=partition.digest(documents[-1]),
        )


@pytest.mark.parametrize("length", [0, 1, 31, 33])
def test_partial_or_overfull_partition_is_not_sealed(length):
    documents = original_documents(33)[:length]
    with pytest.raises(
        partition.HistoryPartitionError, match="partition_exact32_members_required"
    ):
        partition.build_partition_checkpoint_v1(
            scope=SCOPE,
            b3_documents=documents,
            native_registrations=(None,) * length,
            previous_checkpoints=(),
            expected_b3_policy_sha256=SOURCE_POLICY,
            expected_b3_head_sha256=pin("head"),
        )


@pytest.mark.parametrize(
    "change", ["boolean_sequence", "count_decrease", "previous_anchor", "source_policy"]
)
def test_resealed_malformed_original_b3_does_not_pass(change):
    documents = list(original_documents(32))
    value = json.loads(documents[10])
    anchor = json.loads(value["anchor_json"])
    if change == "boolean_sequence":
        value["sequence"] = True
    elif change == "count_decrease":
        anchor["source_facts_through_sequence"] -= 1
    elif change == "previous_anchor":
        anchor["previous_anchor_sha256"] = pin("different_predecessor")
    else:
        value["policy_sha256"] = pin("different_policy")
    value["anchor_json"] = partition.canonical(anchor).decode()
    value["anchor_sha256"] = partition.digest(partition.canonical(anchor))
    documents[10] = partition.canonical(value)
    with pytest.raises(partition.HistoryPartitionError):
        checkpoint(tuple(documents), registrations=(None,) * 32)


def test_cross_partition_capture_conflict_is_not_hidden_by_checkpoint_hash():
    documents = original_documents(64, duplicate_capture_at=33)
    first = checkpoint(documents[:32], registrations=(None,) * 32)
    with pytest.raises(
        partition.HistoryPartitionError,
        match="partition_cross_partition_capture_conflict",
    ):
        checkpoint(
            documents[32:], previous=(first.record_json,), registrations=(None,) * 32
        )


def test_current_head_and_original_custody_binding_are_not_reassigned():
    documents = original_documents(32)
    with pytest.raises(
        partition.HistoryPartitionError, match="partition_original_head_changed"
    ):
        partition.build_partition_checkpoint_v1(
            scope=SCOPE,
            b3_documents=documents,
            native_registrations=(None,) * 32,
            previous_checkpoints=(),
            expected_b3_policy_sha256=SOURCE_POLICY,
            expected_b3_head_sha256=pin("different_current_head"),
        )
    registrations = [registration(raw) for raw in documents]
    changed = original_linkage(documents[1])
    changed["custody_bundle_id"] = original_linkage(documents[0])["custody_bundle_id"]
    registrations[1] = registration(documents[1], changed)
    with pytest.raises(
        partition.HistoryPartitionError, match="partition_custody_binding_conflict"
    ):
        checkpoint(documents, registrations=tuple(registrations))


def test_checkpoint_dto_flags_or_missing_middle_source_cannot_be_used_as_authority():
    documents = original_documents(32)
    result = checkpoint(documents, registrations=(None,) * 32)
    for mutation in ("authority", "missing_middle"):
        value = json.loads(result.record_json)
        if mutation == "authority":
            value["clear_stop_permission"] = True
        else:
            value["members"].pop(15)
        changed = partition.canonical(value)
        with pytest.raises(partition.HistoryPartitionError):
            partition.replay_partition_checkpoints_v1(
                scope=SCOPE,
                checkpoints=(changed,),
                expected_checkpoint_sha256=partition.digest(changed),
                expected_b3_policy_sha256=SOURCE_POLICY,
                expected_b3_head_sha256=partition.digest(documents[-1]),
            )
    with pytest.raises(
        partition.HistoryPartitionError,
        match="partition_exact_checkpoint_bytes_required",
    ):
        partition.replay_partition_checkpoints_v1(
            scope=SCOPE,
            checkpoints=(result,),
            expected_checkpoint_sha256=result.record_sha256,
            expected_b3_policy_sha256=SOURCE_POLICY,
            expected_b3_head_sha256=partition.digest(documents[-1]),
        )


def test_duplicate_nested_keys_and_noncanonical_bytes_fail_before_json_collapse():
    raw = original_documents(1)[0]
    linkage = partition.canonical(original_linkage(raw))
    malformed = linkage.replace(
        b'"monotonic_ns":2001', b'"monotonic_ns":2001,"monotonic_ns":2001', 1
    )
    assert malformed != linkage
    for changed, code in (
        (malformed, "partition_json_duplicate_key"),
        (b" " + linkage, "partition_json_noncanonical"),
    ):
        with pytest.raises(partition.HistoryPartitionError, match=code):
            partition.derive_native_member_registration_v1(
                scope=SCOPE,
                b3_document_raw=raw,
                native_linkage_raw=changed,
                expected_b3_policy_sha256=SOURCE_POLICY,
                expected_b3_event_sha256=partition.digest(raw),
            )


def test_cumulative_chain_budget_is_checked_before_repeated_or_truncated_replay():
    documents = original_documents(32)
    result = checkpoint(documents, registrations=(None,) * 32)
    repeated = (result.record_json,) * (
        partition.MAX_CHAIN_BYTES // len(result.record_json) + 1
    )
    with pytest.raises(
        partition.HistoryPartitionError, match="partition_cumulative_byte_bound"
    ):
        partition.replay_partition_checkpoints_v1(
            scope=SCOPE,
            checkpoints=repeated,
            expected_checkpoint_sha256=result.record_sha256,
            expected_b3_policy_sha256=SOURCE_POLICY,
            expected_b3_head_sha256=partition.digest(documents[-1]),
        )
