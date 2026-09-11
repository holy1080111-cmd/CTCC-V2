"""Synthetic declarations only: no external material is opened or verified."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import HttpUrl, ValidationError

from app.research.external_benchmarks.contracts import LicenseStatus
from app.research.external_benchmarks.strategy_evidence import (
    MAX_METADATA_BYTES,
    AuthorMetric,
    CostBasis,
    CostContext,
    EvidenceAvailability,
    MaterialRole,
    MetricKind,
    MetricWindow,
    PassiveLogic,
    SampleUnit,
    SourceAssertion,
    StrategyEvidenceError,
    StrategyEvidencePack,
    StrategyFamily,
    StrategyProfile,
    freeze_strategy_evidence_pack,
    verify_strategy_evidence_pack,
)
from app.research.external_benchmarks.strategy_evidence import (
    TestType as EvidenceTestType,
)

NOW = datetime(2024, 1, 3, tzinfo=UTC)


def source(role=MaterialRole.REPORT, source_id="source.1") -> SourceAssertion:
    return SourceAssertion(
        source_id=source_id,
        material_role=role,
        original_url=HttpUrl("https://example.test/synthetic-report"),
        author="Synthetic author",
        retrieved_at=NOW - timedelta(hours=1),
        artifact_sha256="a" * 64,
        artifact_byte_size=123,
        license_status=LicenseStatus.REVIEW_REQUIRED,
    )


def known_pack() -> StrategyEvidencePack:
    return StrategyEvidencePack(
        pack_id="synthetic.pack",
        recorded_at=NOW,
        profile=StrategyProfile(
            strategy_id="synthetic.trend",
            name="Synthetic strategy",
            families=(StrategyFamily.TREND_MOMENTUM,),
        ),
        logic=PassiveLogic(summary="Synthetic passive description; never executed."),
        sources=(source(),),
        availability=EvidenceAvailability.E1,
        metrics=(
            AuthorMetric(
                metric_id="reported.win.rate",
                kind=MetricKind.TRADE_WIN_RATE,
                source_id="source.1",
                value=Decimal("0.5"),
                definition="Reported winning trades / all reported closed trades",
                unit="ratio",
                test_type=EvidenceTestType.BACKTEST,
                market="BTCUSDT",
                timeframe="1m",
                sample_window=MetricWindow(
                    start_at=NOW - timedelta(days=2),
                    end_at=NOW - timedelta(days=1),
                ),
                denominator=100,
                sample_unit=SampleUnit.TRADE,
                costs=CostContext(
                    fees=CostBasis.UNKNOWN,
                    funding=CostBasis.UNKNOWN,
                    spread=CostBasis.UNKNOWN,
                    slippage=CostBasis.UNKNOWN,
                    assumptions="The synthetic author did not disclose cost treatment.",
                ),
            ),
        ),
    )


def unknown_pack() -> StrategyEvidencePack:
    return StrategyEvidencePack(
        pack_id="synthetic.unknown",
        recorded_at=NOW,
        profile=StrategyProfile(
            strategy_id="unknown.lead",
            name="Unverified lead",
            classification_missing_reason="Logic not yet available.",
        ),
        logic=PassiveLogic(missing_reason="No original logic material."),
        sources=(
            SourceAssertion(
                source_id="source.unknown",
                material_role=MaterialRole.AUTHOR_CLAIM,
                missing_reason="Original source and artifact have not been obtained.",
            ),
        ),
        metrics=(
            AuthorMetric(
                metric_id="unknown.win.rate",
                kind=MetricKind.TRADE_WIN_RATE,
                source_id="source.unknown",
                missing_reason="No published metric.",
            ),
        ),
    )


def verify_bytes(payload: bytes) -> StrategyEvidencePack:
    return verify_strategy_evidence_pack(
        payload,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )


@pytest.mark.parametrize("factory", [unknown_pack, known_pack])
def test_valid_declarations_round_trip_without_verification_or_authority(
    factory,
) -> None:
    pack = factory()
    frozen = freeze_strategy_evidence_pack(pack)
    assert (
        verify_strategy_evidence_pack(
            frozen.payload,
            expected_sha256=frozen.sha256,
        )
        == pack
    )
    assert frozen.sha256 == pack.canonical_sha256()
    assert pack.ctcc_validation.status == "not_performed"
    assert pack.external_namespace == "external_evidence"
    assert pack.source_authenticity_verified is False
    assert pack.predictive_eligible is False
    assert pack.runtime_consumers == 0
    assert pack.execution_authority is False
    assert pack.promotion_eligible is False
    with pytest.raises(ValidationError):
        pack.execution_authority = True


def test_unknowns_are_not_fabricated_urls_times_hashes_or_zero_performance() -> None:
    pack = unknown_pack()
    assert pack.sources[0].metadata_complete is False
    assert pack.sources[0].original_url is None
    assert pack.sources[0].retrieved_at is None
    assert pack.sources[0].artifact_sha256 is None
    assert pack.metrics[0].value is None
    assert pack.metrics[0].denominator is None
    assert pack.availability == EvidenceAvailability.E0
    assert known_pack().metrics[0].costs.fees == CostBasis.UNKNOWN


@pytest.mark.parametrize("section", ["profile", "logic", "source", "metric"])
def test_missing_information_requires_an_explanation(section) -> None:
    data = unknown_pack().model_dump()
    if section == "profile":
        data[section]["classification_missing_reason"] = None
    else:
        target = data[section] if section == "logic" else data[f"{section}s"][0]
        target["missing_reason"] = None
    with pytest.raises(ValidationError):
        StrategyEvidencePack.model_validate(data)


@pytest.mark.parametrize("section", ["profile", "logic", "source", "metric"])
def test_present_information_cannot_also_claim_to_be_missing(section) -> None:
    data = known_pack().model_dump()
    if section == "profile":
        data[section]["classification_missing_reason"] = "Missing"
    else:
        target = data[section] if section == "logic" else data[f"{section}s"][0]
        target["missing_reason"] = "Missing"
    with pytest.raises(ValidationError):
        StrategyEvidencePack.model_validate(data)


@pytest.mark.parametrize(
    "tier,roles",
    [
        (EvidenceAvailability.E1, (MaterialRole.REPORT,)),
        (EvidenceAvailability.E2, (MaterialRole.TRADES,)),
        (EvidenceAvailability.E3, (MaterialRole.CODE, MaterialRole.DATA)),
        (EvidenceAvailability.E4, (MaterialRole.OOS,)),
        (EvidenceAvailability.E5, (MaterialRole.FORWARD,)),
        (EvidenceAvailability.E5, (MaterialRole.PAPER,)),
        (EvidenceAvailability.E6, (MaterialRole.LIVE,)),
    ],
)
def test_availability_requires_its_own_material_not_cumulative_promotion(
    tier, roles
) -> None:
    data = known_pack().model_dump()
    data.update(
        availability=tier,
        metrics=(),
        sources=tuple(
            source(role, f"source.{index}") for index, role in enumerate(roles)
        ),
    )
    pack = StrategyEvidencePack.model_validate(data)
    assert verify_bytes(freeze_strategy_evidence_pack(pack).payload) == pack
    assert pack.ctcc_validation.status == "not_performed"
    assert pack.predictive_eligible is False and pack.promotion_eligible is False
    data["sources"] = (source(MaterialRole.AUTHOR_CLAIM),)
    with pytest.raises(ValidationError):
        StrategyEvidencePack.model_validate(data)
    data["sources"] = tuple(
        SourceAssertion(
            source_id=f"source.{i}",
            material_role=role,
            missing_reason="Original not available",
        )
        for i, role in enumerate(roles)
    )
    with pytest.raises(ValidationError):
        StrategyEvidencePack.model_validate(data)


@pytest.mark.parametrize("role", [MaterialRole.CODE, MaterialRole.DATA])
def test_e3_requires_both_code_and_input_data(role) -> None:
    data = known_pack().model_dump()
    data.update(
        availability=EvidenceAvailability.E3, sources=(source(role),), metrics=()
    )
    with pytest.raises(ValidationError):
        StrategyEvidencePack.model_validate(data)


@pytest.mark.parametrize(
    "field",
    [
        "definition",
        "unit",
        "test_type",
        "market",
        "timeframe",
        "sample_window",
        "denominator",
        "sample_unit",
        "costs",
    ],
)
def test_numeric_claim_requires_each_context_field(field) -> None:
    data = known_pack().model_dump()
    data["metrics"][0][field] = None
    with pytest.raises(ValidationError):
        StrategyEvidencePack.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("value", 0.5),
        ("value", True),
        ("value", "0.5"),
        ("value", Decimal("NaN")),
        ("value", Decimal("Infinity")),
        ("value", Decimal("-0.01")),
        ("value", Decimal("1.01")),
        ("denominator", True),
        ("denominator", 0),
        ("denominator", "100"),
        ("sample_unit", SampleUnit.WALK_FORWARD_WINDOW),
        ("unit", "percent"),
        ("kind", "promotion_threshold"),
        ("attribution", "ctcc_validation"),
    ],
)
def test_invalid_metrics_do_not_silently_coerce_or_change_meaning(field, value) -> None:
    data = known_pack().model_dump()
    data["metrics"][0][field] = value
    with pytest.raises(ValidationError):
        StrategyEvidencePack.model_validate(data)


def test_window_profitability_has_its_own_denominator() -> None:
    data = known_pack().model_dump()
    data["metrics"][0].update(
        kind=MetricKind.PROFITABLE_WINDOW_RATE,
        sample_unit=SampleUnit.WALK_FORWARD_WINDOW,
        definition="Profitable walk-forward windows / all evaluated windows",
    )
    pack = StrategyEvidencePack.model_validate(data)
    assert verify_bytes(freeze_strategy_evidence_pack(pack).payload) == pack
    data["metrics"][0]["sample_unit"] = SampleUnit.TRADE
    with pytest.raises(ValidationError):
        StrategyEvidencePack.model_validate(data)


@pytest.mark.parametrize(
    "attack",
    [
        "duplicate_source",
        "duplicate_metric",
        "unknown_source",
        "incomplete_source",
        "late_sample",
        "late_retrieval",
        "reversed_window",
        "naive_time",
        "non_utc",
    ],
)
def test_source_links_and_chronology_fail_closed(attack) -> None:
    data = known_pack().model_dump()
    if attack == "duplicate_source":
        data["sources"] *= 2
    elif attack == "duplicate_metric":
        data["metrics"] *= 2
    elif attack == "unknown_source":
        data["metrics"][0]["source_id"] = "unknown"
    elif attack == "incomplete_source":
        data["sources"][0].update(artifact_sha256=None, missing_reason="No original")
        data["availability"] = EvidenceAvailability.E0
    elif attack == "late_sample":
        data["metrics"][0]["sample_window"]["end_at"] = NOW
    elif attack == "late_retrieval":
        data["sources"][0]["retrieved_at"] = NOW + timedelta(seconds=1)
    elif attack == "reversed_window":
        data["metrics"][0]["sample_window"]["start_at"] = NOW
    elif attack == "naive_time":
        data["recorded_at"] = NOW.replace(tzinfo=None)
    else:
        data["recorded_at"] = NOW.astimezone(timezone(timedelta(hours=8)))
    with pytest.raises(ValidationError):
        StrategyEvidencePack.model_validate(data)


@pytest.mark.parametrize(
    "url", ["http://example.test/report", "https://user:pass@example.test/r"]
)
def test_sources_reject_insecure_or_credential_bearing_urls(url) -> None:
    data = known_pack().model_dump()
    data["sources"][0]["original_url"] = HttpUrl(url)
    with pytest.raises(ValidationError):
        StrategyEvidencePack.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("runtime_consumers", 1),
        ("runtime_consumers", False),
        ("execution_authority", True),
        ("execution_authority", 0),
        ("promotion_eligible", True),
        ("reference_only", False),
        ("source_authenticity_verified", True),
        ("predictive_eligible", True),
        ("external_namespace", "ctcc_validation"),
        ("ctcc_e", "E6"),
    ],
)
def test_top_level_authority_and_namespace_cannot_change(field, value) -> None:
    forged = known_pack().model_copy(update={field: value})
    with pytest.raises(StrategyEvidenceError):
        freeze_strategy_evidence_pack(forged)


@pytest.mark.parametrize(
    "section", ["profile", "logic", "sources", "metrics", "ctcc_validation"]
)
def test_nested_copy_tampering_is_revalidated(section) -> None:
    pack = known_pack()
    item = getattr(pack, section)
    replacement = (
        (item[0].model_copy(update={"execution_authority": True}),)
        if type(item) is tuple
        else item.model_copy(update={"execution_authority": True})
    )
    with pytest.raises(StrategyEvidenceError):
        freeze_strategy_evidence_pack(pack.model_copy(update={section: replacement}))


@pytest.mark.parametrize(
    "update",
    [
        {"status": "passed"},
        {"ctcc_e": "E6"},
        {"metrics": ()},
        {"independently_verified": True},
    ],
)
def test_ctcc_comparison_cannot_accept_recomputed_or_verified_results(update) -> None:
    data = known_pack().model_dump()
    data["ctcc_validation"].update(update)
    with pytest.raises(ValidationError):
        StrategyEvidencePack.model_validate(data)


@pytest.mark.parametrize("pin", [None, "", "A" * 64, "0" * 64, "abc"])
def test_verification_requires_a_valid_external_pin(pin) -> None:
    frozen = freeze_strategy_evidence_pack(known_pack())
    with pytest.raises(StrategyEvidenceError):
        verify_strategy_evidence_pack(frozen.payload, expected_sha256=pin)


def test_pin_cannot_be_replaced_by_a_self_rehashed_modified_pack() -> None:
    pack = known_pack()
    frozen = freeze_strategy_evidence_pack(pack)
    changed = freeze_strategy_evidence_pack(
        pack.model_copy(update={"pack_id": "changed"})
    )
    assert changed.sha256 != frozen.sha256
    with pytest.raises(StrategyEvidenceError):
        verify_strategy_evidence_pack(changed.payload, expected_sha256=frozen.sha256)
    with pytest.raises(TypeError):
        verify_strategy_evidence_pack(frozen.payload)


@pytest.mark.parametrize(
    "attack",
    ["whitespace", "duplicate", "nested_duplicate", "wrong_schema", "extra", "nan"],
)
def test_self_rehashed_invalid_encodings_are_rejected(attack) -> None:
    frozen = freeze_strategy_evidence_pack(known_pack())
    payload = frozen.payload
    if attack == "whitespace":
        payload += b"\n"
    elif attack == "duplicate":
        payload = b'{"pack_id":"forged",' + payload[1:]
    elif attack == "nested_duplicate":
        payload = payload.replace(b'"profile":{', b'"profile":{"name":"duplicate",', 1)
    else:
        data = json.loads(payload)
        if attack == "wrong_schema":
            data["schema_version"] = "ctcc.mie.gate3.evidence.v1"
        elif attack == "extra":
            data["ctcc_e"] = "E6"
        else:
            data["metrics"][0]["value"] = float("nan")
        payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(StrategyEvidenceError):
        verify_bytes(payload)


@pytest.mark.parametrize(
    "payload",
    [b"", b"x" * (MAX_METADATA_BYTES + 1), bytearray(b"{}")],
    ids=["empty", "oversized", "mutable"],
)
def test_input_is_bounded_immutable_bytes(payload) -> None:
    with pytest.raises(StrategyEvidenceError):
        verify_strategy_evidence_pack(payload, expected_sha256="a" * 64)


def test_freeze_enforces_total_envelope_size_not_only_per_field_limits() -> None:
    data = unknown_pack().model_dump()
    data["sources"] = tuple(
        {**data["sources"][0], "source_id": f"source.{i}", "missing_reason": "x" * 2000}
        for i in range(32)
    )
    data["metrics"] = tuple(
        {
            **data["metrics"][0],
            "metric_id": f"metric.{i}",
            "source_id": "source.0",
            "missing_reason": "x" * 2000,
            "definition": "x" * 2000,
        }
        for i in range(64)
    )
    pack = StrategyEvidencePack.model_validate(data)
    with pytest.raises(StrategyEvidenceError):
        freeze_strategy_evidence_pack(pack)


def test_role_paths_and_collection_limits_are_fixed() -> None:
    pack = known_pack()
    for update in (
        {"file_roles": ("../escape.json", *pack.file_roles[1:])},
        {"sources": pack.sources * 33},
        {"metrics": pack.metrics * 65},
    ):
        with pytest.raises(StrategyEvidenceError):
            freeze_strategy_evidence_pack(pack.model_copy(update=update))


def test_top_level_and_nested_subclass_extras_cannot_bypass_freeze() -> None:
    class DerivedPack(StrategyEvidencePack):
        pass

    class DerivedSource(SourceAssertion):
        hidden_authority: bool = True

    pack = known_pack()
    with pytest.raises(StrategyEvidenceError):
        freeze_strategy_evidence_pack(DerivedPack.model_validate(pack.model_dump()))
    forged_source = DerivedSource.model_validate(pack.sources[0].model_dump())
    with pytest.raises(StrategyEvidenceError):
        freeze_strategy_evidence_pack(
            pack.model_copy(update={"sources": (forged_source,)})
        )
