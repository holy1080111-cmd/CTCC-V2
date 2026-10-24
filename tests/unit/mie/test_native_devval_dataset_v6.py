"""Synthetic I/O and real aggregation only; no native/PIT/OOS acceptance."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import ClockStamp, canonical, sha
from app.mie.features import FeatureBar
from app.mie.validation import native_availability_v6 as native
from app.mie.validation import native_devval_dataset_v6 as dataset
from app.mie.validation.contracts import DatasetPartition, PartitionWindow
from app.mie.validation.development_validation_source_plan_v1 import (
    DevelopmentValidationSourceCoordinateV1,
    DevelopmentValidationSourcePlanV1,
)
from app.mie.validation.post_publication_availability_v6 import (
    FutureAvailabilityDecisionV6,
)
from app.mie.validation.replay import PointInTimeBar
from app.public_market_source.public_market_receipts import PublicMinuteCapturePlanV1
from tests.unit.mie.test_native_stage1_retention_v6 import (  # noqa: F401 - declared fixture dependencies.
    flow6,
    raw_case,
)

START = datetime(2026, 10, 20, tzinfo=UTC)
END = START + timedelta(minutes=5040)
SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")


def _ns(value):
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (
        (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    ) * 1000


@pytest.fixture(scope="module")
def synthetic_grid():
    """Two symbols, four bounded captures, complete 21 x 4H warmup bars.

    Source blobs intentionally identify synthetic I/O. Positive computational
    tests replace ONLY the lineage decoder with the retained synthetic map;
    no such decoder seam exists in the production native builder API.
    """
    coordinates, groups = [], []
    for symbol in SYMBOLS:
        for start, count in ((START, 2880), (START + timedelta(minutes=2880), 2160)):
            ordinal = len(groups)
            capture = PublicMinuteCapturePlanV1(
                origin="https://openapi.okx.com",
                instrument_id=symbol,
                start_ns=_ns(start),
                end_ns=_ns(start + timedelta(minutes=count)),
                expected_rows=count,
                created_ns=_ns(END + timedelta(seconds=1)),
            )
            coordinates.append(
                DevelopmentValidationSourceCoordinateV1(
                    ordinal=ordinal,
                    partition="development",
                    purpose="historical_warmup",
                    capture_plan=capture,
                    capture_plan_sha256=capture.canonical_sha256(),
                )
            )
            blobs = {
                name: canonical({"synthetic_io_only": name, "ordinal": ordinal})
                for name in ("receipt", "evidence", "bundle")
            }
            decision = FutureAvailabilityDecisionV6(
                evidence_sha256=sha(blobs["evidence"]),
                bundle_sha256=sha(blobs["bundle"]),
                stage1_forensic_sha256=sha(f"forensic:{ordinal}".encode()),
                ordinal_claim_sha256=sha(f"claim:{ordinal}".encode()),
                cutoff=ClockStamp(
                    utc_ns=_ns(END) + 200_000_000, monotonic_ns=1_200_000_000
                ),
                source_rows_sha256=sha(f"source:{ordinal}".encode()),
                row_count=count,
            )
            rows = []
            available = END + timedelta(milliseconds=100)
            for offset in range(1, count + 1):
                minute = int((start - START).total_seconds()) // 60 + offset
                price = (
                    Decimal(100)
                    + Decimal(minute) / Decimal(100)
                    + Decimal((minute // 31) % 7)
                )
                rows.append(
                    PointInTimeBar(
                        source_row_id=f"synthetic:{ordinal}:{offset}",
                        source_row_sha256=sha(f"row:{ordinal}:{offset}".encode()),
                        instrument_id=symbol,
                        available_at=available,
                        bar=FeatureBar(
                            closed_at=start + timedelta(minutes=offset),
                            open=price,
                            high=price + 2,
                            low=price - 1,
                            close=price + Decimal("0.25"),
                            volume=Decimal(1 + minute % 11),
                        ),
                    )
                )
            values = {
                name + "_base64": base64.b64encode(raw).decode("ascii")
                for name, raw in blobs.items()
            }
            values.update({name + "_sha256": sha(raw) for name, raw in blobs.items()})
            groups.append(
                dataset.NativeDevvalSourceGroupV6(
                    ordinal=ordinal,
                    capture_plan=capture,
                    decision=decision,
                    decision_sha256=decision.canonical_sha256(),
                    rows=tuple(rows),
                    available_at=available,
                    **values,
                )
            )
    source = DevelopmentValidationSourcePlanV1(
        collection_id="synthetic.devval.only",
        origin="https://openapi.okx.com",
        planned_at=END + timedelta(seconds=1),
        coordinates=tuple(coordinates),
        expected_rows=10080,
    )
    plan = dataset.NativeDevvalDatasetPlanV6(
        source_plan_sha256=source.canonical_sha256(),
        instruments=SYMBOLS,
        windows=(
            PartitionWindow(
                partition=DatasetPartition.DEVELOPMENT, start_at=START, end_at=END
            ),
        ),
    )
    return plan, source, tuple(groups)


@pytest.fixture
def synthetic_io(monkeypatch, synthetic_grid):
    plan, source, groups = synthetic_grid
    retained = {group.ordinal: group.canonical_sha256() for group in groups}

    def decode(group):
        if retained.get(group.ordinal) != group.canonical_sha256():
            raise dataset.NativeDatasetError("synthetic_source_readback_changed")

    monkeypatch.setattr(dataset, "_validate_group_lineage", decode)
    return {
        "plan": plan,
        "expected_plan_sha256": plan.canonical_sha256(),
        "source_plan": source,
        "groups": groups,
        "source_inventory_sha256": "a" * 64,
    }


def test_complete_two_symbol_grid_materializes_real_aggregates_and_features(
    synthetic_io,
):
    frozen = dataset.materialize_native_devval_dataset_v6(**synthetic_io)
    result = frozen.contract
    assert len(result.groups) == 4 and len(result.series) == 2
    assert sum(len(series.minutes) for series in result.series) == 10080
    for series in result.series:
        assert [len(frame.bars) for frame in series.aggregation.timeframes] == [
            336,
            84,
            21,
        ]
        assert [
            feature.feature_snapshot.horizon.seconds for feature in series.features
        ] == [900, 3600, 14400]
        assert all(feature.source_row_count == 21 for feature in series.features)
        assert all(
            row.availability.available_at == END + timedelta(milliseconds=100)
            for row in series.minutes
        )
        assert series.aggregation.plan.volume_unit == "contracts"
    assert result.frozen_as_of == END + timedelta(milliseconds=200)
    assert result.predictive_oos_eligible is result.execution_authority is False


def test_dataset_replay_preserves_cutoff_and_exact_output_under_decimal_context(
    synthetic_io,
):
    groups = synthetic_io["groups"]
    assert any(
        "." in str(row.bar.close) and str(row.bar.close).endswith("0")
        for group in groups
        for row in group.rows
    )
    for group in groups:
        normalized = dataset._group(group)
        assert normalized.rows == group.rows  # Exact numeric values are retained.
        assert normalized.decision == group.decision
        # All original source/evidence blobs, hashes and cutoffs remain bound
        # to the same canonical group; only Decimal representation is stable.
        assert normalized.canonical_json_bytes() == group.canonical_json_bytes()
    first = dataset.materialize_native_devval_dataset_v6(**synthetic_io)
    same_context = dataset.replay_native_devval_dataset_v6(
        first.payload, expected_sha256=first.sha256
    )
    assert first.payload == same_context.payload and first.sha256 == same_context.sha256
    del same_context
    with localcontext() as context:
        context.prec = 7
        second = dataset.replay_native_devval_dataset_v6(
            first.payload, expected_sha256=first.sha256
        )
    assert first.payload == second.payload and first.sha256 == second.sha256


def test_real_lineage_decoder_rejects_the_explicit_synthetic_io_blobs(synthetic_grid):
    with pytest.raises(ValueError):
        dataset._validate_group_lineage(synthetic_grid[2][0])


@pytest.mark.parametrize(
    "kind",
    (
        "missing_group",
        "reordered_group",
        "wrong_plan",
        "missing_row",
        "conflicting_row",
        "late_row",
        "wrong_unit",
        "wrong_environment",
        "wrong_source_pin",
    ),
)
def test_incomplete_changed_or_late_source_fails_closed(
    synthetic_io, monkeypatch, kind
):
    values = dict(synthetic_io)
    groups = list(values["groups"])
    if kind == "missing_group":
        groups.pop()
    elif kind == "reordered_group":
        groups[0], groups[1] = groups[1], groups[0]
    elif kind == "wrong_plan":
        values["expected_plan_sha256"] = "f" * 64
    else:
        original = groups[0]
        changed = original.model_dump(mode="python")
        if kind == "missing_row":
            changed["rows"] = changed["rows"][:-1]
        elif kind == "conflicting_row":
            changed["rows"] = (
                changed["rows"][0],
                changed["rows"][0],
                *changed["rows"][2:],
            )
            # Exercise real coordinate/conflict checks separately from the
            # synthetic retained-source decoder's equally valid prior denial.
            monkeypatch.setattr(dataset, "_validate_group_lineage", lambda group: None)
        elif kind == "late_row":
            changed["available_at"] = END + timedelta(seconds=1)
            changed["rows"] = tuple(
                {**row, "available_at": changed["available_at"]}
                for row in changed["rows"]
            )
        elif kind == "wrong_unit":
            changed["capture_plan"]["volume_unit"] = "base_currency"
        elif kind == "wrong_environment":
            changed["capture_plan"]["environment"] = "demo"
        elif kind == "wrong_source_pin":
            changed["evidence_base64"] = base64.b64encode(b"changed").decode("ascii")
        with pytest.raises(ValueError):
            groups[0] = dataset.NativeDevvalSourceGroupV6.model_validate(changed)
            values["groups"] = tuple(groups)
            dataset.materialize_native_devval_dataset_v6(**values)
        return
    values["groups"] = tuple(groups)
    with pytest.raises(ValueError):
        dataset.materialize_native_devval_dataset_v6(**values)


def test_saved_cutoff_cannot_be_replaced_even_with_a_new_outer_hash(synthetic_io):
    frozen = dataset.materialize_native_devval_dataset_v6(**synthetic_io)
    changed = frozen.contract.model_copy(
        update={"frozen_as_of": END + timedelta(seconds=2)}
    )
    raw = changed.canonical_json_bytes()
    with pytest.raises(ValueError, match="replay_changed"):
        dataset.replay_native_devval_dataset_v6(raw, expected_sha256=sha(raw))


def test_portable_dataset_cannot_claim_execution_or_predictive_authority(synthetic_io):
    frozen = dataset.materialize_native_devval_dataset_v6(**synthetic_io)
    values = frozen.contract.model_dump(mode="python")
    # The same complete otherwise-valid dataset must reject both Boolean
    # promotion and coercion. Missing unrelated fields cannot make this pass.
    for claim in (True, 1, 0, "false"):
        with pytest.raises(ValueError, match="portable_authority_forbidden"):
            dataset.NativeDevvalDatasetV6.model_validate(
                {**values, "execution_authority": claim}
            )


@pytest.mark.asyncio
async def test_portable_report_is_not_a_native_collection_owner(synthetic_grid):
    plan, source, groups = synthetic_grid
    with pytest.raises(ValueError):
        await dataset.build_native_devval_dataset_v6(
            owner=groups[0],
            source_plan=source,
            plan=plan,
            expected_plan_sha256=plan.canonical_sha256(),
        )


def _lease_record(monkeypatch, *, elapsed):
    """Explicit synthetic clock/file/health I/O; the lease arithmetic is real."""
    ready = ClockStamp(utc_ns=_ns(END), monotonic_ns=10_000_000_000)
    decision = SimpleNamespace(contract=SimpleNamespace(cutoff=ready))
    reader = SimpleNamespace(read=lambda *args, **kwargs: None)
    owner = SimpleNamespace(
        claim=SimpleNamespace(
            state=SimpleNamespace(reader=reader), claim=SimpleNamespace(ordinal=0)
        )
    )
    evidence = SimpleNamespace(
        contract=SimpleNamespace(
            final_ready=ready,
            final_health=SimpleNamespace(raw_bytes=lambda: b"synthetic-health"),
        )
    )
    monkeypatch.setattr(native, "_recheck", lambda owner: None)
    monkeypatch.setattr(
        native,
        "_stamp",
        lambda: ClockStamp(
            utc_ns=ready.utc_ns + elapsed, monotonic_ns=ready.monotonic_ns + elapsed
        ),
    )
    monkeypatch.setattr(native, "health_fresh_at", lambda *args: None)
    monkeypatch.setattr(native, "_devval_runtime_check_v6", lambda: None)
    return {
        "original": (owner, evidence, object(), "a" * 64),
        "decision": decision,
        "attempted": False,
        "replayed": False,
        "journal": None,
    }


def test_original_five_second_lease_is_not_replaced_with_acquisition_budget(
    monkeypatch,
):
    record = _lease_record(monkeypatch, elapsed=native.CONSUMER_LEASE_NS + 100)
    with pytest.raises(ValueError):
        native._devval_original_check(record)


def test_original_acquisition_guard_still_runs_on_dataset_binding(monkeypatch):
    record = _lease_record(monkeypatch, elapsed=100)
    calls = []

    def expired(owner):
        calls.append(owner)
        raise native.NativeAvailabilityError("synthetic_original_acquisition_expired")

    monkeypatch.setattr(native, "_recheck", expired)
    with pytest.raises(ValueError, match="acquisition_expired"):
        native._devval_original_check(record)
    assert len(calls) == 1


def test_final_replay_attempt_is_reserved_before_await_and_cannot_be_reused():
    binding = native._DevvalBindingV6(native._ISSUER)
    native._DEVVAL_LIVE[binding] = {"attempted": False}
    coroutine = native.replay_native_devval_group_v6(binding)
    try:
        assert native._DEVVAL_LIVE[binding]["attempted"] is True
        with pytest.raises(ValueError):
            native.replay_native_devval_group_v6(binding)
    finally:
        coroutine.close()
        native.discard_native_devval_group_v6(binding)
    with pytest.raises(ValueError):
        native.replay_native_devval_group_v6(binding)


def test_source_function_substitution_is_rejected_before_native_acquisition(
    monkeypatch,
):
    monkeypatch.setattr(
        native, "consume_native_devval_group_v6", lambda *args, **kwargs: None
    )
    with pytest.raises(ValueError, match="function_changed"):
        dataset._check_code()


@pytest.mark.parametrize(
    "mutation", ("changed_bytes", "extra_file", "changed_manifest")
)
def test_whole_prefix_inventory_detects_source_change_without_acquisition(
    tmp_path, monkeypatch, mutation
):
    source = tmp_path / "app/mie"
    source.mkdir(parents=True)
    original = source / "only.py"
    original.write_bytes(b"# original source\n")
    manifest = tmp_path / "MANIFEST.sha256"
    manifest.write_bytes(
        (sha(original.read_bytes()) + "  app/mie/only.py\n").encode("ascii")
    )
    monkeypatch.setattr(dataset, "_ROOT", tmp_path)
    monkeypatch.setattr(dataset, "_PREFIXES", ("app/mie/",))
    monkeypatch.setattr(dataset, "_SOURCE_CLOSURE_FILES", ("app/mie/only.py",))
    before = dataset._source_inventory()
    if mutation == "changed_bytes":
        original.write_bytes(b"# changed source\n")
    elif mutation == "extra_file":
        (source / "extra.py").write_bytes(b"# unexpected source\n")
    else:
        manifest.write_bytes(manifest.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        after = dataset._source_inventory()
        dataset._need(after == before, "native_devval_source_inventory_changed")


def test_no_clobber_publication_keeps_accepted_dataset_on_late_retry(
    synthetic_io, tmp_path
):
    frozen = dataset.materialize_native_devval_dataset_v6(**synthetic_io)
    receipt = dataset.publish_native_devval_dataset_v6(
        root=tmp_path, payload=frozen.payload, expected_sha256=frozen.sha256
    )
    originals = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert sha(originals["receipt-v6.json"]) == receipt
    assert originals["dataset-v6.json"] == frozen.payload
    with pytest.raises(ValueError, match="publication_not_empty"):
        dataset.publish_native_devval_dataset_v6(
            root=tmp_path, payload=frozen.payload, expected_sha256=frozen.sha256
        )
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == originals


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ("capture", "final_replay"))
@pytest.mark.parametrize(
    "defect",
    (
        "denial_helper",
        "record_validator",
        "feature_leaf",
        "feature_context",
        "kwdefault_in_place",
        "kwdefault_replace",
        "aggregate_policy",
        "original_lease",
        "feature_constructor",
        "window_constructor",
        "parent_model_constructor",
        "generated_state_constructor",
        "validator_dispatch",
    ),
)
async def test_runtime_leaf_or_default_changed_during_await_is_rejected_before_next_use(
    synthetic_grid, monkeypatch, phase, defect
):
    """Explicit synthetic I/O baseline is pinned BEFORE the awaited mutation.

    No production owner registry is injected. The real builder/guard runs;
    deliberately fixed fake capture/consume/replay/materialization providers
    let this test reach both await boundaries without native or PG acceptance.
    No pin snapshot is refreshed after the defect.
    """
    import asyncio

    from pydantic import BaseModel

    from app.mie.features import models as real_models
    from app.mie.validation import batch_replay as real_batch
    from app.mie.validation import replay as real_replay

    plan, source, groups = synthetic_grid
    calls = {"consume": 0, "finish": 0, "mutation": 0}

    def mutate():
        calls["mutation"] += 1
        if defect == "denial_helper":
            monkeypatch.setattr(native, "_require", lambda value: None)
        elif defect == "record_validator":
            monkeypatch.setattr(
                real_replay, "_validate_records", lambda rows, **kwargs: rows
            )
        elif defect == "feature_leaf":
            monkeypatch.setattr(
                real_replay,
                "mathematical_feature_snapshot",
                lambda *args, **kwargs: None,
            )
        elif defect == "feature_context":
            monkeypatch.setattr(real_replay, "_feature_context", lambda: None)
        elif defect == "kwdefault_in_place":
            monkeypatch.setitem(
                real_replay.replay_features_at.__kwdefaults__,
                "signal_alpha",
                Decimal("0.99"),
            )
        elif defect == "kwdefault_replace":
            monkeypatch.setattr(
                real_replay.replay_features_at,
                "__kwdefaults__",
                {
                    **real_replay.replay_features_at.__kwdefaults__,
                    "signal_alpha": Decimal("0.99"),
                },
            )
        elif defect == "aggregate_policy":
            monkeypatch.setattr(real_batch, "TARGETS", (("15m", 900), ("1H", 3600)))
        elif defect == "original_lease":
            monkeypatch.setattr(native, "CONSUMER_LEASE_NS", 120_000_000_000)
        elif defect in (
            "feature_constructor",
            "window_constructor",
            "parent_model_constructor",
            "generated_state_constructor",
        ):
            target = {
                "feature_constructor": real_models.MathematicalFeatureSnapshot,
                "window_constructor": real_models.FeatureWindow,
                "parent_model_constructor": BaseModel,
                "generated_state_constructor": native._State,
            }[defect]
            original_init = target.__init__

            def changed_init(self, *args, **kwargs):
                return original_init(self, *args, **kwargs)

            # Semantically equivalent replacement is enough to violate the
            # fixed source binding; it must be detected before its next use.
            monkeypatch.setattr(target, "__init__", changed_init)
        elif defect == "validator_dispatch":
            monkeypatch.setattr(
                real_models.FeatureWindow, "__pydantic_validator__", object()
            )

    async def capture_once(self, *, ordinal, expected_previous_receipt_sha256s):
        assert expected_previous_receipt_sha256s == tuple(
            group.receipt_sha256 for group in groups[:ordinal]
        )
        await asyncio.sleep(0)
        if phase == "capture":
            mutate()
        return ordinal

    def consume(eligibility, *, expected_source_plan_sha256):
        calls["consume"] += 1
        assert expected_source_plan_sha256 == source.canonical_sha256()
        return ("synthetic-binding", eligibility), groups[eligibility].model_dump(
            mode="python"
        )

    async def replay(binding):
        await asyncio.sleep(0)
        if phase == "final_replay":
            mutate()
        return "same-synthetic-journal"

    def finish(bindings):
        calls["finish"] += 1

    monkeypatch.setattr(
        dataset.DevelopmentValidationAvailabilityOwnerV6, "capture_once", capture_once
    )
    monkeypatch.setattr(dataset, "_source_inventory", lambda: ("synthetic-inventory",))
    monkeypatch.setattr(
        dataset,
        "materialize_native_devval_dataset_v6",
        lambda **kwargs: "synthetic-materialized-only",
    )
    monkeypatch.setattr(native, "consume_native_devval_group_v6", consume)
    monkeypatch.setattr(native, "replay_native_devval_group_v6", replay)
    monkeypatch.setattr(native, "finish_native_devval_groups_v6", finish)
    monkeypatch.setattr(native, "discard_native_devval_group_v6", lambda binding: None)
    # Fixed provider admission is test-only and occurs BEFORE acquisition and
    # BEFORE mutate(), never after a changed source/default is observed.
    monkeypatch.setattr(
        native, "_DEVVAL_FUNCTION_PINS", native._devval_function_inventory_v6()
    )
    monkeypatch.setattr(dataset, "_NATIVE_HOOK_PINS", native._DEVVAL_FUNCTION_PINS)
    monkeypatch.setattr(
        dataset,
        "_FUNCTION_PINS",
        native._devval_capture_runtime_pins_v6(
            dataset._DATASET_DECLARED_MODULES, dataset._DATASET_DECLARED_CLASSES
        ),
    )
    dataset._check_code()
    owner = object.__new__(dataset.DevelopmentValidationAvailabilityOwnerV6)
    with pytest.raises(dataset.NativeDatasetError, match="unavailable"):
        await dataset.build_native_devval_dataset_v6(
            owner=owner,
            source_plan=source,
            plan=plan,
            expected_plan_sha256=plan.canonical_sha256(),
        )
    assert calls["mutation"] == 1 and calls["finish"] == 0
    assert calls["consume"] == (0 if phase == "capture" else len(groups))


@pytest.fixture
async def complete_original_v6_group(flow6):  # noqa: F811 -- explicitly imported pytest fixture.
    """Real original V6 parser/consumer with explicitly synthetic native/DB I/O.

    This uses the existing fixture only to obtain a valid complete retained
    receipt, evidence and Stage1 bundle. It never inserts a native owner or
    resets the new guards to accept the receipt mutations under test.
    """
    token = await flow6.instance().capture_once(
        ordinal=0, expected_previous_receipt_sha256s=()
    )
    _owner, evidence, bundle, receipt_pin = native._READY[token]
    decision, rows = native.consume_for_current_future_decision_v6(
        token, expected_evidence_sha256=evidence.sha256
    )
    receipt_raw = (flow6.root / "batch-00000000/receipt.json").read_bytes()
    assert sha(receipt_raw) == receipt_pin
    coordinate = flow6.plan.coordinate(0)
    return dataset.NativeDevvalSourceGroupV6(
        ordinal=0,
        capture_plan=coordinate.capture_plan,
        receipt_base64=base64.b64encode(receipt_raw).decode("ascii"),
        receipt_sha256=receipt_pin,
        evidence_base64=base64.b64encode(evidence.payload).decode("ascii"),
        evidence_sha256=evidence.sha256,
        bundle_base64=base64.b64encode(bundle.payload).decode("ascii"),
        bundle_sha256=bundle.sha256,
        decision=decision.contract,
        decision_sha256=decision.sha256,
        rows=rows,
        available_at=rows[0].available_at,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    (
        "cancelled_reason",
        "unresolved_reason",
        "failed_forensics",
        "cancelled_forensics",
        "not_started_forensics",
        "empty_files",
        "missing_file",
        "duplicate_file",
        "reordered_files",
        "changed_file_hash",
    ),
)
async def test_rehashed_contradictory_success_receipt_fails_full_original_lineage(
    complete_original_v6_group, defect
):
    import json

    original = complete_original_v6_group
    # Establish an otherwise valid baseline through the REAL new lineage
    # verifier, not the computational synthetic decoder used by grid tests.
    dataset._validate_group_lineage(original)
    receipt = json.loads(base64.b64decode(original.receipt_base64, validate=True))
    assert receipt["reason"] == "captured" and receipt["forensic_status"] == "completed"
    assert len(receipt["forensic_files"]) >= 2
    if defect == "cancelled_reason":
        receipt["reason"] = "cancelled"
    elif defect == "unresolved_reason":
        receipt["reason"] = "unresolved"
    elif defect == "failed_forensics":
        receipt["forensic_status"] = "failed"
    elif defect == "cancelled_forensics":
        receipt["forensic_status"] = "cancelled"
    elif defect == "not_started_forensics":
        receipt["forensic_status"] = "not_started"
    elif defect == "empty_files":
        receipt["forensic_files"] = []
    elif defect == "missing_file":
        receipt["forensic_files"] = receipt["forensic_files"][1:]
    elif defect == "duplicate_file":
        receipt["forensic_files"].append(dict(receipt["forensic_files"][0]))
    elif defect == "reordered_files":
        receipt["forensic_files"].reverse()
    elif defect == "changed_file_hash":
        receipt["forensic_files"][0]["sha256"] = "0" * 64
    raw = canonical(receipt)
    # Rehash BOTH the canonical receipt and canonical enclosing group. A
    # different outer hash must not hide contradictory completion semantics.
    changed = original.model_copy(
        update={
            "receipt_base64": base64.b64encode(raw).decode("ascii"),
            "receipt_sha256": sha(raw),
        }
    )
    group_raw = changed.canonical_json_bytes()
    assert sha(group_raw) != original.canonical_sha256()
    replayed = dataset.NativeDevvalSourceGroupV6.model_validate_json(group_raw)
    assert replayed.receipt_sha256 == sha(raw)
    with pytest.raises(ValueError, match="receipt_completion_or_forensics_changed"):
        dataset._validate_group_lineage(replayed)


def test_effective_class_routes_stay_stable_for_normal_models_methods_and_live_registry(
    synthetic_grid,
):
    """Normal model work and live map contents must not invalidate source pins."""
    from app.mie.features.models import FeatureWindow, MathematicalFeatureSnapshot

    plan = synthetic_grid[0]
    owners = (type(plan), FeatureWindow, MathematicalFeatureSnapshot, native._State)
    before = tuple(native._devval_class_routes_pin_v6(owner) for owner in owners)
    model = type(plan).model_validate(plan.model_dump(mode="python"))
    assert type(plan).model_validate_json(model.model_dump_json()) == plan
    # Each classmethod access creates an ordinary fresh bound wrapper. Only
    # its defining descriptor/function and class identity are source bindings.
    assert native._devval_member_pin_v6(
        type(plan), "model_validate"
    ) == native._devval_member_pin_v6(type(plan), "model_validate")
    registry_before = native._devval_data_pin_v6(native._DEVVAL_LIVE)
    binding = native._DevvalBindingV6(native._ISSUER)
    try:
        native._DEVVAL_LIVE[binding] = {"synthetic_stability_probe_only": True}
        assert native._devval_data_pin_v6(native._DEVVAL_LIVE) == registry_before
        assert (
            tuple(native._devval_class_routes_pin_v6(owner) for owner in owners)
            == before
        )
    finally:
        native.discard_native_devval_group_v6(binding)
    assert native._devval_data_pin_v6(native._DEVVAL_LIVE) == registry_before


@pytest.mark.parametrize("slot", ("__new__", "__init__", "model_post_init"))
def test_inherited_or_new_subclass_constructor_slot_is_not_hidden_by_class_identity(
    monkeypatch, slot
):
    from app.mie.features.models import FeatureWindow

    marker = native._DEVVAL_CLASS_ROUTE_BINDING_V6
    pins = (
        (FeatureWindow, marker, native._devval_member_pin_v6(FeatureWindow, marker)),
    )
    original = getattr(FeatureWindow, slot)
    if slot == "__new__":

        def replacement(cls, *args, **kwargs):
            return original(cls)

        monkeypatch.setattr(FeatureWindow, slot, staticmethod(replacement))
    else:

        def replacement(self, *args, **kwargs):
            return original(self, *args, **kwargs)

        monkeypatch.setattr(FeatureWindow, slot, replacement)
    with pytest.raises(native.NativeAvailabilityError, match="runtime_binding_changed"):
        native._devval_check_runtime_pins_v6(pins)


@pytest.mark.parametrize(
    "case",
    (
        "windows_distinct_stable_ctime",
        "windows_missing_birthtime",
        "windows_conflicting_birthtime",
        "path_ctime_drift",
        "descriptor_ctime_drift",
        "cross_device_drift",
        "cross_inode_drift",
        "cross_size_drift",
        "cross_mtime_drift",
        "content_length_drift",
        "posix_equal_ctime",
        "posix_distinct_ctime",
        *(
            kind + "_" + phase
            for kind in (
                "hardlink",
                "reparse_attribute",
                "reparse_tag",
                "nonregular_mode",
                "changed_regular_mode",
            )
            for phase in ("before", "opened", "closed", "after")
        ),
    ),
)
def test_bounded_source_reader_preserves_both_windows_ctime_observations(
    monkeypatch, case
):
    """Synthetic metadata; no OS, fixture or baseline timestamps are altered."""
    from io import BytesIO

    values = {
        "st_dev": 17630174255628276314,
        "st_ino": 281474978553934,
        "st_mode": 0o100666,
        "st_nlink": 1,
        "st_size": 18,
        "st_mtime_ns": 1792856478220282400,
        "st_ctime_ns": 1792856478219282300,
        "st_birthtime_ns": 1792856478219282300,
        "st_file_attributes": 32,
        "st_reparse_tag": 0,
    }
    metadata = [SimpleNamespace(**values) for _ in range(4)]
    before, opened, closed, after = metadata
    opened.st_ctime_ns = closed.st_ctime_ns = 1792856478220282400
    platform = "nt"
    raw = b"# original source\n"
    if case == "windows_missing_birthtime":
        del opened.st_birthtime_ns
    elif case == "windows_conflicting_birthtime":
        opened.st_birthtime_ns += 1
    elif case == "path_ctime_drift":
        after.st_ctime_ns += 1
    elif case == "descriptor_ctime_drift":
        closed.st_ctime_ns += 1
    elif case.startswith("cross_"):
        attribute = {
            "cross_device_drift": "st_dev",
            "cross_inode_drift": "st_ino",
            "cross_size_drift": "st_size",
            "cross_mtime_drift": "st_mtime_ns",
        }[case]
        setattr(opened, attribute, getattr(opened, attribute) + 1)
        setattr(closed, attribute, getattr(closed, attribute) + 1)
    elif case == "content_length_drift":
        raw += b"!"
    elif case.startswith(("hardlink_", "reparse_", "nonregular_", "changed_regular_")):
        kind, phase = case.rsplit("_", 1)
        value = {"before": before, "opened": opened, "closed": closed, "after": after}[
            phase
        ]
        field, replacement = {
            "hardlink": ("st_nlink", 2),
            "reparse_attribute": ("st_file_attributes", 32 | 0x400),
            "reparse_tag": ("st_reparse_tag", 0xA000000C),
            "nonregular_mode": ("st_mode", 0o040777),
            "changed_regular_mode": ("st_mode", 0o100600),
        }[kind]
        setattr(value, field, replacement)
    elif case.startswith("posix_"):
        platform = "posix"
        for value in metadata:
            del value.st_birthtime_ns
        if case == "posix_equal_ctime":
            opened.st_ctime_ns = closed.st_ctime_ns = before.st_ctime_ns

    class Stream(BytesIO):
        def fileno(self):
            return 123

    class Source:
        parents = ()

        def __init__(self):
            self.path_samples = iter((before, after))

        def is_absolute(self):
            return True

        def lstat(self):
            return before

        def stat(self):
            return next(self.path_samples)

        def open(self, mode):
            assert mode == "rb"
            return Stream(raw)

    descriptor_samples = iter((opened, closed))

    def descriptor_stat(descriptor):
        assert descriptor == 123
        return next(descriptor_samples)

    # Replace only this module alias, never the process-wide os object.
    monkeypatch.setattr(
        dataset, "os", SimpleNamespace(name=platform, fstat=descriptor_stat)
    )
    if case in ("windows_distinct_stable_ctime", "posix_equal_ctime"):
        data, observed, digest = dataset._regular_bytes(Source(), 100)
        assert data == raw and digest == sha(raw)
        assert observed[0][4] == before.st_ctime_ns
        assert observed[1][4] == opened.st_ctime_ns
        assert observed[2] == (values["st_birthtime_ns"] if platform == "nt" else None)
        assert (
            observed[0][5:]
            == observed[1][5:]
            == (
                values["st_mode"],
                values["st_nlink"],
                values["st_file_attributes"],
                values["st_reparse_tag"],
            )
        )
    else:
        with pytest.raises(ValueError):
            dataset._regular_bytes(Source(), 100)
