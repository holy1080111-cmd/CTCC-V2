"""Versioned full-public raw replay into the shared G1 arithmetic evaluator.

This diagnostic does not establish native acquisition or grant qualification,
reservation or execution permission. No supplied context or PASS is accepted.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from gc import get_referents
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import data
from app.trade_qualification import public_market_collector_v2 as public
from app.trade_qualification import quote_collector_v2 as quotes
from app.trade_qualification.event_models import Digest
from app.trade_qualification.executable_quote_v2 import POLICY_SHA256
from app.trade_qualification.market_bridge_v2 import public_market_context_v2
from app.trade_qualification.models import (
    GateAssessment,
    QualificationGate,
    QualificationModel,
    ReportId,
    Text,
)


class DataQualificationResultV2(QualificationModel):
    schema_version: Literal["ctcc.data_qualification.v2"]
    report_id: ReportId
    instrument_id: Text
    evaluated_at: datetime
    gate: GateAssessment
    public_bundle_sha256: Digest
    quote_bundle_sha256: Digest
    quote_profile_sha256: Digest
    quote_transport_policy_sha256: Digest
    quote_inspection_json: Annotated[
        str, StringConstraints(strip_whitespace=False), Field(max_length=65536)
    ]
    quote_inspection_sha256: Digest
    funding_pair_json: Annotated[
        str, StringConstraints(strip_whitespace=False), Field(max_length=2048)
    ]
    funding_pair_sha256: Digest
    policy: data.DataQualificationPolicy | None = None
    policy_sha256: Digest | None = None
    reference_sha256: Digest | None = None
    source_json: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=False),
            Field(max_length=data.MAX_SOURCE_BYTES),
        ]
        | None
    ) = None
    source_sha256: Digest | None = None
    source_authenticity_verified: Literal[False] = False
    original_source_verified: Literal[False] = False
    execution_authority: Literal[False] = False
    admission: Literal["DENY"] = "DENY"

    _time = field_validator("evaluated_at")(data._utc)

    @field_validator(
        "source_authenticity_verified",
        "original_source_verified",
        "execution_authority",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("data_v2_cannot_grant_authority")
        return value

    @model_validator(mode="after")
    def checked_record(self):
        if (
            self.gate.gate != QualificationGate.DATA
            or self.gate.report_id != self.report_id
            or self.quote_profile_sha256 != POLICY_SHA256
            or self.quote_transport_policy_sha256 != quotes.TRANSPORT_POLICY_SHA256
        ):
            raise ValueError("data_v2_identity_or_profile_mismatch")
        for raw, pin, maximum in (
            (self.quote_inspection_json, self.quote_inspection_sha256, 65536),
            (self.funding_pair_json, self.funding_pair_sha256, 2048),
        ):
            encoded = raw.encode()
            if len(encoded) > maximum or sha(encoded) != pin:
                raise ValueError("data_v2_source_binding_mismatch")
            if canonical(decode(encoded, maximum)) != encoded:
                raise ValueError("data_v2_source_binding_not_canonical")
        if (
            self.policy is not None
            and data._sha(data._canonical(self.policy)) != self.policy_sha256
        ):
            raise ValueError("data_v2_policy_binding_mismatch")
        if self.source_json is not None and (
            len(self.source_json.encode()) > data.MAX_SOURCE_BYTES
            or data._sha(self.source_json.encode()) != self.source_sha256
        ):
            raise ValueError("data_v2_rebuilt_source_binding_mismatch")
        if self.gate.passed and any(
            value is None
            for value in (
                self.policy,
                self.policy_sha256,
                self.reference_sha256,
                self.source_json,
                self.source_sha256,
            )
        ):
            raise ValueError("data_v2_pass_requires_complete_rebuilt_source")
        return self

    @property
    def passed(self):
        return self.gate.passed

    @property
    def evaluation_sha256(self):
        return sha(data._canonical(_validated_record_v2(self)))


def _validated_record_v2(value):
    """Reject hidden fields and unbounded inputs before nested serialization."""
    if type(value) is not DataQualificationResultV2:
        raise ValueError("data_v2_exact_result_required")
    if (
        set(value.__dict__) != set(DataQualificationResultV2.model_fields)
        or value.__pydantic_extra__
    ):
        raise ValueError("data_v2_exact_result_fields_required")
    for name, item in value.__dict__.items():
        if name in {"gate", "policy"}:
            continue
        if name in {
            "source_authenticity_verified",
            "original_source_verified",
            "execution_authority",
        }:
            if type(item) is not bool or item is not False:
                raise ValueError("data_v2_cannot_grant_authority")
        elif name == "evaluated_at":
            if type(item) is not datetime or item.tzinfo is not UTC:
                raise ValueError("data_v2_exact_utc_result_required")
        elif item is not None and (type(item) is not str):
            raise ValueError("data_v2_exact_result_scalar_required")
        elif (
            name
            not in {
                "quote_inspection_json",
                "funding_pair_json",
                "source_json",
            }
            and item is not None
            and len(item) > 512
        ):
            raise ValueError("data_v2_result_scalar_bound")
    for raw, maximum in (
        (value.quote_inspection_json, 65536),
        (value.funding_pair_json, 2048),
        (value.source_json, data.MAX_SOURCE_BYTES),
    ):
        if raw is not None and (
            type(raw) is not str
            or len(raw) > maximum
            or len(raw.encode("utf-8")) > maximum
        ):
            raise ValueError("data_v2_result_source_bound")
    _preflight_gate_v2(value.gate)
    data._revalidate(value.gate, GateAssessment)
    if value.policy is not None:
        data._bounded_scalars(value.policy, data.DataQualificationPolicy)
    return data._revalidate(value, DataQualificationResultV2)


def _preflight_gate_v2(value):
    """Bound native containers and primitives before a gate serializer runs."""
    if (
        type(value) is not GateAssessment
        or set(value.__dict__) != set(GateAssessment.model_fields)
        or value.__pydantic_extra__
    ):
        raise ValueError("data_v2_exact_gate_required")
    if type(value.gate) is not QualificationGate or type(value.passed) is not bool:
        raise ValueError("data_v2_exact_gate_scalar_required")
    for text, maximum in (
        (value.report_id, 96),
        (value.code, 96),
        (value.reason, 512),
    ):
        if type(text) is not str or not 1 <= len(text) <= maximum:
            raise ValueError("data_v2_gate_scalar_bound")
    measurements = value.measured_values
    if type(measurements) is MappingProxyType:
        # A native proxy can wrap a foreign Mapping. Inspect its native referent
        # before calling len/items, either of which could run that Mapping's code.
        referents = get_referents(measurements)
        if len(referents) != 1 or type(referents[0]) is not dict:
            raise ValueError("data_v2_exact_measurement_container_required")
        measurements = referents[0]
    elif type(measurements) is not dict:
        raise ValueError("data_v2_exact_measurement_container_required")
    if not 1 <= len(measurements) <= 32:
        raise ValueError("data_v2_measurement_count_bound")
    for key, item in measurements.items():
        if type(key) is not str or not 1 <= len(key) <= 512:
            raise ValueError("data_v2_measurement_key_bound")
        if type(item) is str:
            if len(item) > 512:
                raise ValueError("data_v2_measurement_text_bound")
        elif type(item) is int:
            if abs(item) > 10**40:
                raise ValueError("data_v2_measurement_integer_bound")
        elif type(item) is Decimal:
            if (
                not item.is_finite()
                or len(item.as_tuple().digits) > 128
                or abs(item.as_tuple().exponent) > 128
                or len(str(item)) > 128
            ):
                raise ValueError("data_v2_measurement_decimal_bound")
        elif item is not None and type(item) is not bool:
            raise ValueError("data_v2_exact_measurement_scalar_required")


def evaluate_public_market_data_v2(
    packet, *, expected_bundle_sha256, policy, evaluated_at
):
    """Replay the complete raw packet, then recompute G1 at the declared cutoff.

    Native ownership stays in the private acquisition registry. The supplied
    time is a replay cutoff. Missing or malformed full raw provenance raises a
    denial; a bare quote, domain context or old packet cannot replace it.
    """
    now = data._utc(evaluated_at)
    context = public_market_context_v2(
        packet, expected_bundle_sha256=expected_bundle_sha256, evaluated_at=now
    )
    # The bridge already replayed all component bytes, transport bounds and
    # schedule. These bindings retain the inseparable fundingTime/rate pair.
    document = decode(packet.packet_json, public.MAX_PACKET_BYTES)
    quote = context.quote
    pair_raw = canonical(
        {
            "schema_version": "ctcc.g1_funding_pair.v2",
            "instrument_id": quote.instrument_id,
            "rate": str(quote.funding.forecast.rate),
            "settlement_at": quote.funding.forecast.settlement_at.isoformat(),
            "raw_body_sha256": quote.funding.body_sha256,
            "canonical_sha256": quote.funding.canonical_sha256,
            "exchange_data_return_at": quote.funding.exchange_data_return_at.isoformat(),
            "body_completed_at": quote.funding.completed_at.isoformat(),
            "rate_generated_at": None,
            "historical_first_available_at": None,
        }
    )
    evidence = {}
    measured = {
        "quality_recomputed": False,
        "analysis_recomputed": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
    }

    def finish(code, reason):
        return DataQualificationResultV2(
            schema_version="ctcc.data_qualification.v2",
            report_id=quote.report_id,
            instrument_id=quote.instrument_id,
            evaluated_at=now,
            public_bundle_sha256=context.packet_sha256,
            quote_bundle_sha256=document["quote_sha256"],
            quote_profile_sha256=POLICY_SHA256,
            quote_transport_policy_sha256=quotes.TRANSPORT_POLICY_SHA256,
            quote_inspection_json=context.quote_inspection_json.decode(),
            quote_inspection_sha256=sha(context.quote_inspection_json),
            funding_pair_json=pair_raw.decode(),
            funding_pair_sha256=sha(pair_raw),
            **evidence,
            gate=GateAssessment(
                report_id=quote.report_id,
                gate=QualificationGate.DATA,
                passed=code == "passed",
                code=code,
                reason=reason,
                measured_values=measured,
            ),
        )

    prepared = data._prepare_market_data(
        context.market, instrument_id=quote.instrument_id, policy=policy, now=now
    )
    evidence.update(prepared.evidence)
    if prepared.code is not None:
        return finish(prepared.code, prepared.reason)
    # The fixed V2 profile has a distinct funding age. A stricter declared G1
    # quote age continues to constrain ticker/mark source and receipt times.
    if any(
        now - stamp > timedelta(seconds=prepared.policy.maximum_quote_age_seconds)
        for stamp in (
            quote.ticker.source_generated_at,
            quote.ticker.headers_received_at,
            quote.ticker.body_completed_at,
            quote.mark.exchange_data_return_at,
            quote.mark.headers_received_at,
            quote.mark.body_completed_at,
        )
    ):
        return finish(
            "stale_market_data", "Ticker or mark exceeds the explicit G1 age policy."
        )
    tail = data._evaluate_data_tail(
        prepared.market,
        report_id=quote.report_id,
        instrument_id=quote.instrument_id,
        current_quote=data._ValidatedQuoteOperands(
            quote.ticker.bid,
            quote.ticker.ask,
            quote.mark.price,
            quote.funding.forecast.rate,
        ),
        reference=context.reference,
        checked_policy=prepared.policy,
        now=now,
    )
    measured.update(tail.measured)
    evidence.update(tail.evidence)
    return finish(tail.code, tail.reason)


def verify_public_market_data_v2(
    result, packet, *, expected_bundle_sha256, policy, evaluated_at
):
    """Replay actual raw inputs; a self-signed G1 result is not permission."""
    checked = _validated_record_v2(result)
    replayed = evaluate_public_market_data_v2(
        packet,
        expected_bundle_sha256=expected_bundle_sha256,
        policy=policy,
        evaluated_at=evaluated_at,
    )
    if data._canonical(checked) != data._canonical(replayed):
        raise ValueError("data_v2_replay_mismatch")
    return replayed
