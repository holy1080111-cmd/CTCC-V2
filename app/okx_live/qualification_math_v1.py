"""Pure Live G1--G11 and fixed-original recheck on unchanged public raw bytes.

The anonymous Global packet retains its original report, origin and environment.
Only the Live envelope binds its separate report. No source owner, account fact,
Arm, reservation, evidence publication or submission permit is created here.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Context, Decimal, localcontext
from hashlib import sha256

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.okx_live import finite_portfolio_v1 as finite_risk
from app.public_market_source import public_runtime_journal as journal
from app.trade_qualification import current_conditions as conditions
from app.trade_qualification import current_economics_v2 as costs
from app.trade_qualification import data, engine, fixed_protection, location
from app.trade_qualification import native_live_public_source_v1 as public
from app.trade_qualification import owned_current_original_prefix_v1 as families
from app.trade_qualification import public_source_runtime as transport
from app.trade_qualification.events import TriggerDetection, extract_trigger
from app.trade_qualification.fixed_original_event_path_v1 import (
    evaluate_fixed_original_event_path_v1,
    join_confirmed_original_path_v1,
)
from app.trade_qualification.models import (
    EntryQualificationResult,
    EntryZone,
    GateAssessment,
    QualificationGate,
)
from app.trade_qualification.portfolio import evaluate_live_portfolio
from app.trade_qualification.service import QualificationIntent
from app.trade_qualification.timing import (
    TIMING_POLICIES,
    evaluate_timing,
    event_identity,
)


class LiveQualificationMathError(ValueError):
    """Fixed DATA refusal, never raw filesystem, account or transport contents."""


def _need(value, code="live_qualification_math_invalid"):
    if value is not True:
        raise LiveQualificationMathError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class LiveQualificationMathV1:
    source: public.LivePublicEvaluationV1
    intent: QualificationIntent
    policy: engine.PreEvidencePolicy
    consumed_event_keys: frozenset[str] | None
    risk_inputs: (
        engine.LivePortfolioInputs | finite_risk.LiveFinitePortfolioInputsV1 | None
    )
    result: EntryQualificationResult
    detection: TriggerDetection | None
    original_zone: EntryZone | None
    selection_audit_json: str | None
    family_history_json: bytes | None
    source_json: str | None
    source_sha256: str | None
    risk_operands_json: bytes | None
    receipt_json: bytes
    original: "LiveQualificationMathV1 | None" = field(default=None, repr=False)
    publication_candidate_sha256: str | None = None
    environment: str = field(default="live", init=False)
    execution_authority: bool = field(default=False, init=False)
    source_authenticity_verified: bool = field(default=False, init=False)
    account_complete: bool = field(default=False, init=False)
    atomic_risk_reserved: bool = field(default=False, init=False)

    @property
    def evaluation_sha256(self):
        return sha(self.receipt_json)

    @property
    def market_math_complete(self):
        return len(self.result.gates) >= 10 and all(
            gate.passed for gate in self.result.gates[:10]
        )

    @property
    def pre_evidence_complete(self):
        return len(self.result.gates) == 11 and all(g.passed for g in self.result.gates)

    @property
    def candidate_geometry_sha256(self):
        """Stable geometry identity, deliberately distinct from a G12 digest."""
        return sha(
            canonical(
                {
                    "intent": self.intent.model_dump(mode="json"),
                    "detection": None
                    if self.detection is None
                    else self.detection.model_dump(mode="json"),
                    "zone": None
                    if self.original_zone is None
                    else self.original_zone.model_dump(mode="json"),
                    "selection_audit_json": self.selection_audit_json,
                    "stop_loss": str(self.result.stop_loss),
                    "take_profit": str(self.result.take_profit),
                    "policy": self.policy.model_dump(mode="json"),
                }
            )
        )


class _RawFiles:
    """Bounded byte-only replay reader; no disk, network or native capability."""

    def __init__(self, files):
        _need(type(files) is tuple and 2 <= len(files) <= 2 * journal.MAX_EVENTS + 2)
        self._files = {}
        total = 0
        pin = sha256(b"ctcc.native_live.public_raw_inventory.v1\n")
        for item in files:
            _need(type(item) is public.LivePublicRawFileV1)
            _need(type(item.name) is str and item.name not in self._files)
            _need(type(item.raw) is bytes and len(item.raw) <= journal.MAX_RAW)
            _need(sha(item.raw) == item.sha256)
            total += len(item.raw)
            _need(total <= journal.MAX_TOTAL)
            self._files[item.name] = item.raw
        for name in sorted(self._files):
            raw = self._files[name]
            pin.update(canonical([name, len(raw), sha(raw)]) + b"\n")
        self.inventory_sha256 = pin.hexdigest()

    def names(self):
        return tuple(self._files)

    def read(self, name, maximum):
        _need(type(name) is str and name in self._files)
        raw = self._files[name]
        _need(type(maximum) is int and 0 <= len(raw) <= maximum)
        return raw


def _source(value):
    _need(type(value) is public.LivePublicEvaluationV1)
    _need(type(value.packet) is public.production.GlobalPublicComponentPacketV1)
    _need(type(value.market) is MarketSnapshot)
    _need(type(value.quote) is public.live_quote.LiveExecutableQuoteV1)
    _need(
        value.environment == "live"
        and value.source_environment == journal.GLOBAL_ENVIRONMENT
    )
    _need(
        value.execution_authority
        is value.account_complete
        is value.historical_availability_verified
        is False
    )
    files = _RawFiles(value.raw_files)
    _need(files.inventory_sha256 == value.raw_inventory_sha256)
    _need(sha(files.read("summary.json", journal.MAX_RAW)) == value.journal_sha256)
    summary, events, packet = transport.replay_public_runtime(
        files, expected_plan_sha256=value.plan_sha256
    )
    plan = decode(files.read("plan.json", journal.MAX_RAW), journal.MAX_RAW)
    staged = plan["schema_version"] == journal.GLOBAL_STAGED_PLAN
    _need(staged or plan["schema_version"] == journal.GLOBAL_INITIAL_PLAN)
    _need(summary["disposition"] == "captured")
    packet_json = packet.packet_json
    _need(
        packet_json == value.packet.packet_json
        and packet.policy_sha256 == public._WIRE_POLICY_SHA256
    )
    _need(packet.instrument_id == value.instrument_id)
    receipt = decode(value.receipt_json, 16384)
    _need(canonical(receipt) == value.receipt_json)
    _need(
        receipt["environment"] == "live"
        and receipt["source_environment"] == packet.environment
        and receipt["source_report_id"] == packet.report_id
        and receipt["report_id"] == value.report_id
        and receipt["instrument_id"] == value.instrument_id
        and receipt["stage"] == value.stage
        and receipt["candidate_sha256"] == value.candidate_sha256
        and receipt["publication_receipt_sha256"] == value.publication_receipt_sha256
        and receipt["packet_sha256"] == sha(packet_json)
        and receipt["plan_sha256"] == value.plan_sha256
        and receipt["journal_sha256"] == value.journal_sha256
        and receipt["raw_inventory_sha256"] == value.raw_inventory_sha256
        and receipt["raw_packet_relabelled"] is False
        and receipt["execution_authority"] is False
        and receipt["account_complete"] is False
        and receipt["historical_availability_verified"] is False
        and receipt["raw_file_count"] == len(value.raw_files)
        and receipt["raw_total_bytes"] == sum(len(item.raw) for item in value.raw_files)
        and receipt["expires_at"] == value.expires_at.isoformat()
        and receipt["policy_sha256"]
        == (public.STAGED_POLICY_SHA256 if staged else public.POLICY_SHA256)
    )
    now = data._utc(value.evaluated_at)
    _need(
        utc_from_ns(receipt["readback_completed"]["utc_ns"]) <= now < value.expires_at
    )
    barrier = receipt["publication_barrier"]
    if staged:
        _need(
            value.stage == "post_g12"
            and receipt["schema_version"]
            == "ctcc.native_live.production_public_staged_envelope.v2"
            and plan["stage"] == "post_g12_staged_public"
            and plan["original_publication"]
            == {
                "report_id": value.report_id,
                "candidate_sha256": value.candidate_sha256,
                "receipt_sha256": value.publication_receipt_sha256,
                "barrier": barrier,
                "policy_sha256": public.STAGED_POLICY_SHA256,
            }
        )
        ohlc = [
            event["metadata"]
            for event in events
            if event["kind"] == "ohlc_stage_complete"
        ]
        account = [
            event["metadata"]
            for event in events
            if event["kind"] == "account_stage_ready"
        ]
        _need(len(ohlc) == len(account) == 1)
        _need(
            receipt["ohlc_completed"] == ohlc[0]["completed"]
            and receipt["ohlc_prefix_sha256"] == account[0]["ohlc_prefix_sha256"]
            and receipt["account_completed"] == account[0]["completed"]
            and receipt["account_source_revision_sha256"]
            == account[0]["source_revision_sha256"]
        )
    else:
        _need(receipt["schema_version"] == public._SCHEMA)
    _need(value.stage in {"initial", "post_g12"})
    if value.stage == "initial":
        _need(
            barrier is None
            and value.publication_barrier is None
            and value.candidate_sha256 is None
        )
    else:
        _need(
            value.publication_barrier is not None and value.candidate_sha256 is not None
        )
        _need(barrier == value.publication_barrier.model_dump())
    starts = [
        e["metadata"]["started"]
        for e in events
        if e["kind"] in {"request", "ws_connect"}
    ]
    _need(bool(starts))
    for started in starts:
        for before in (receipt["capture_started"], barrier):
            if before is not None:
                _need(
                    all(
                        started[name] > before[name]
                        for name in ("utc_ns", "monotonic_ns")
                    )
                )
    # The staged packet was independently reconstructed from the entire raw
    # journal in this call. No projected candle is shared with the owner view.
    series = public._copy_locally_replayed_candle_series(packet) if staged else None
    market, quote, reference = public._project(
        packet, report_id=value.report_id, at=now, replayed_series=series
    )
    _need(
        market == value.market and quote == value.quote and reference == value.reference
    )
    return market, quote, reference


def _selection(detection, market, analysis, policy, entry, now):
    return families._select_owned_family_protection_v2(
        detection,
        market,
        analysis,
        observed_at=now,
        entry=entry,
        tick_size=policy.prefix.tick_size,
        **{k: v for k, v in policy.protection.model_dump().items() if k != "policy_id"},
    )


def _copy_risk_inputs(value, policy):
    if type(value) is finite_risk.LiveFinitePortfolioInputsV1:
        finite_risk._exact_tree(value)
        checked = finite_risk.LiveFinitePortfolioInputsV1.model_validate_json(
            value.model_dump_json(round_trip=True), strict=True
        )
        _need(checked.claims.risk_policy == policy.portfolio)
        return checked
    return engine._copy(value, engine.LivePortfolioInputs)


def _risk_inputs_digest(value):
    if type(value) is finite_risk.LiveFinitePortfolioInputsV1:
        finite_risk._exact_tree(value)
        return sha(canonical(value.model_dump(mode="json", round_trip=True)))
    return engine._hash(value)


def _sample_risk(inputs, *, intent, policy, entry, stop, cost, now):
    if type(inputs) is finite_risk.LiveFinitePortfolioInputsV1:
        _need(inputs.claims.risk_policy == policy.portfolio)
        return finite_risk.evaluate_live_finite_portfolio_v1(
            inputs,
            report_id=intent.report_id,
            instrument_id=intent.instrument_id,
            direction=intent.direction,
            candidate_entry=entry,
            stop_loss=stop,
            round_trip_cost_per_base=cost,
            current_time=now,
        )
    _need(type(inputs) is engine.LivePortfolioInputs)
    return evaluate_live_portfolio(
        report_id=intent.report_id,
        instrument_id=intent.instrument_id,
        direction=intent.direction,
        candidate_entry=entry,
        stop_loss=stop,
        round_trip_cost_per_base=cost,
        requested_contracts=inputs.requested_contracts,
        requested_leverage=inputs.requested_leverage,
        instrument=inputs.instrument,
        account=inputs.account,
        authority=inputs.authority,
        policy=policy.portfolio,
        current_time=now,
    )


def evaluate_live_qualification_math_v1(
    source, *, intent, policy, consumed_event_keys, risk_inputs=None
):
    """Initial same-source event/bracket calculation; every result is DATA only."""
    return _evaluate(
        source,
        intent=intent,
        policy=policy,
        consumed_event_keys=consumed_event_keys,
        risk_inputs=risk_inputs,
        original=None,
    )


def recheck_live_qualification_math_v1(
    source,
    *,
    original,
    consumed_event_keys,
    publication_candidate_sha256,
    risk_inputs=None,
):
    """Replay the frozen original first; a better fresh quote cannot repair it."""
    _need(type(original) is LiveQualificationMathV1 and original.original is None)
    prior = verify_live_qualification_math_v1(original)
    return _recheck_verified_original_data_v2(
        source,
        original=prior,
        consumed_event_keys=consumed_event_keys,
        publication_candidate_sha256=publication_candidate_sha256,
        risk_inputs=risk_inputs,
    )


def _recheck_verified_original_data_v2(
    source, *, original, consumed_event_keys, publication_candidate_sha256, risk_inputs
):
    """Same CURRENT mathematics; DATA alone never proves original ownership.

    The public replay API first recomputes the original above. The private
    original-frame coordinator may instead prove its exact pre-G12 full replay
    through its source-owned one-use original-math proof. Neither this helper
    nor its returned DATA can reserve risk, Arm or submit an order.
    """
    prior = original
    _need(type(prior) is LiveQualificationMathV1 and prior.original is None)
    _need(type(source) is public.LivePublicEvaluationV1)
    _need(prior.market_math_complete, "live_original_candidate_did_not_pass")
    _need(
        type(publication_candidate_sha256) is str
        and len(publication_candidate_sha256) == 64
        and all(c in "0123456789abcdef" for c in publication_candidate_sha256)
    )
    _need(
        source.stage == "post_g12"
        and source.candidate_sha256 == publication_candidate_sha256
        and source.invocation is prior.source.invocation
        and source.publication_barrier is not None
        and utc_from_ns(source.publication_barrier.utc_ns) > prior.source.evaluated_at,
        "live_current_original_binding_changed",
    )
    if consumed_event_keys is not None:
        _need(
            type(consumed_event_keys) is frozenset
            and consumed_event_keys.issuperset(prior.consumed_event_keys),
            "live_consumed_event_history_regressed",
        )
    return _evaluate(
        source,
        intent=prior.intent,
        policy=prior.policy,
        consumed_event_keys=consumed_event_keys,
        risk_inputs=risk_inputs,
        original=prior,
    )


def verify_live_qualification_math_v1(value):
    """Recompute all public bytes, indicators, gates and original operands."""
    _need(type(value) is LiveQualificationMathV1)
    _need(
        value.environment == "live"
        and value.execution_authority is False
        and value.source_authenticity_verified is False
        and value.account_complete is False
        and value.atomic_risk_reserved is False
    )
    _need(
        value.publication_candidate_sha256
        == (None if value.original is None else value.source.candidate_sha256)
    )
    if value.original is None:
        replayed = evaluate_live_qualification_math_v1(
            value.source,
            intent=value.intent,
            policy=value.policy,
            consumed_event_keys=value.consumed_event_keys,
            risk_inputs=value.risk_inputs,
        )
    else:
        replayed = recheck_live_qualification_math_v1(
            value.source,
            original=value.original,
            consumed_event_keys=value.consumed_event_keys,
            risk_inputs=value.risk_inputs,
            publication_candidate_sha256=value.publication_candidate_sha256,
        )
    _need(
        value.receipt_json == replayed.receipt_json and value.result == replayed.result,
        "live_qualification_math_replay_mismatch",
    )
    _need(
        (
            value.detection,
            value.original_zone,
            value.selection_audit_json,
            value.family_history_json,
            value.source_json,
            value.source_sha256,
            value.risk_operands_json,
        )
        == (
            replayed.detection,
            replayed.original_zone,
            replayed.selection_audit_json,
            replayed.family_history_json,
            replayed.source_json,
            replayed.source_sha256,
            replayed.risk_operands_json,
        ),
        "live_qualification_math_replay_mismatch",
    )
    return replayed


def _evaluate(source, *, intent, policy, consumed_event_keys, risk_inputs, original):
    _need(type(intent) is QualificationIntent)
    intent = QualificationIntent.model_validate_json(
        canonical(intent.model_dump(mode="json")), strict=True
    )
    policy = engine._copy(policy, engine.PreEvidencePolicy)
    if consumed_event_keys is not None:
        _need(
            type(consumed_event_keys) is frozenset and len(consumed_event_keys) <= 4096
        )
        _need(
            all(
                type(key) is str
                and len(key) == 64
                and all(c in "0123456789abcdef" for c in key)
                for key in consumed_event_keys
            )
        )
    if risk_inputs is not None:
        risk_inputs = _copy_risk_inputs(risk_inputs, policy)
    _need(type(source) is public.LivePublicEvaluationV1)
    _need(
        (source.report_id, source.instrument_id)
        == (intent.report_id, intent.instrument_id)
    )
    now = data._utc(source.evaluated_at)
    values = {
        "report_id": intent.report_id,
        "symbol": source.market.symbol,
        "strategy": intent.strategy,
        "direction": intent.direction,
        "evaluated_at": now,
        "candidate_entry": intent.candidate_entry,
        "raw_score": 0,
        "effective_score": 0,
    }
    gates, evidence = [], {}
    detection = zone = audit = history = risk_operands = None
    fixed_hash = path_hash = None
    family = intent.strategy not in conditions._BASE_STRATEGIES

    def gate(kind, code, reason, measured):
        gates.append(
            GateAssessment(
                report_id=intent.report_id,
                gate=kind,
                passed=code == "passed",
                code=code,
                reason=reason,
                measured_values=measured,
            )
        )
        return code == "passed"

    def finish():
        # Family extensions preserve an actual Unknown router label and its
        # explicit history policy; they never turn Unknown into Trend.
        result_type = (
            families.OwnedFamilyEntryQualificationResultV2
            if family and "family_policy_sha256" in values
            else EntryQualificationResult
        )
        result = result_type(**values, gates=tuple(gates))
        receipt = canonical(
            {
                "schema_version": "ctcc.live_qualification_math.v1",
                "environment": "live",
                "source_environment": source.source_environment,
                "source_report_id": source.packet.report_id,
                "report_id": intent.report_id,
                "instrument_id": intent.instrument_id,
                "stage": source.stage,
                "evaluated_at": now.isoformat(),
                "public_receipt_sha256": sha(source.receipt_json),
                "packet_sha256": source.packet.bundle_sha256,
                "raw_inventory_sha256": source.raw_inventory_sha256,
                "intent": intent.model_dump(mode="json"),
                "policy": policy.model_dump(mode="json"),
                "consumed_event_keys": None
                if consumed_event_keys is None
                else sorted(consumed_event_keys),
                "risk_inputs_sha256": None
                if risk_inputs is None
                else _risk_inputs_digest(risk_inputs),
                "result": result.model_dump(mode="json"),
                "source_sha256": evidence.get("source_sha256"),
                "detection": None
                if detection is None
                else detection.model_dump(mode="json"),
                "original_zone": None if zone is None else zone.model_dump(mode="json"),
                "selection_audit_json": audit,
                "family_history_sha256": None if history is None else sha(history),
                "risk_operands": None
                if risk_operands is None
                else decode(risk_operands),
                "fixed_protection_sha256": fixed_hash,
                "event_path_sha256": path_hash,
                "original_sha256": None
                if original is None
                else original.evaluation_sha256,
                "account_complete": False,
                "source_authenticity_verified": False,
                "execution_authority": False,
                "atomic_risk_reserved": False,
            }
        )
        return LiveQualificationMathV1(
            source,
            intent,
            policy,
            consumed_event_keys,
            risk_inputs,
            result,
            detection,
            zone,
            audit,
            history,
            evidence.get("source_json"),
            evidence.get("source_sha256"),
            risk_operands,
            receipt,
            original,
            None if original is None else source.candidate_sha256,
        )

    try:
        market, quote, reference = _source(source)
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
        gate(
            QualificationGate.DATA,
            "live_public_raw_replay_refused",
            "Original Live envelope and anonymous raw source cannot be replayed.",
            {"raw_replay_satisfied": False, "execution_authority": False},
        )
        return finish()
    prepared = data._prepare_market_data(
        market, instrument_id=intent.instrument_id, policy=policy.prefix.data, now=now
    )
    evidence.update(prepared.evidence)
    if prepared.code is not None:
        gate(
            QualificationGate.DATA,
            prepared.code,
            prepared.reason,
            {"source_recomputed": False},
        )
        return finish()
    if any(
        not timedelta(0)
        <= now - at
        <= timedelta(seconds=prepared.policy.maximum_quote_age_seconds)
        for at in (
            quote.ticker.source_generated_at,
            quote.ticker.headers_received_at,
            quote.ticker.body_completed_at,
            quote.mark.exchange_data_return_at,
            quote.mark.headers_received_at,
            quote.mark.body_completed_at,
        )
    ):
        gate(
            QualificationGate.DATA,
            "stale_market_data",
            "Ticker and mark retain the explicit G1 age ceiling.",
            {"quote_fresh": False},
        )
        return finish()
    tail = data._evaluate_data_tail(
        prepared.market,
        report_id=intent.report_id,
        instrument_id=intent.instrument_id,
        current_quote=data._ValidatedQuoteOperands(
            quote.ticker.bid,
            quote.ticker.ask,
            quote.mark.price,
            quote.funding.forecast.rate,
        ),
        reference=reference,
        checked_policy=prepared.policy,
        now=now,
        retain_current_analysis=original is not None,
    )
    evidence.update(tail.evidence)
    if not gate(QualificationGate.DATA, tail.code, tail.reason, tail.measured):
        return finish()
    raw = decode(evidence["source_json"].encode(), data.MAX_SOURCE_BYTES)
    market = MarketSnapshot.model_validate_json(canonical(raw["market"]), strict=True)
    analysis = MultiTimeframeAnalysis.model_validate_json(
        canonical(raw["analysis"]), strict=True
    )
    timing_policy = TIMING_POLICIES[intent.strategy]
    if original is None:
        _need(source.stage == "initial", "live_initial_stage_required")
        if family:
            detection = extract_trigger(
                market,
                analysis,
                report_id=intent.report_id,
                strategy=intent.strategy,
                direction=intent.direction,
                observed_at=now,
                trigger_ttl_seconds=timing_policy.trigger_ttl_seconds,
            )
    else:
        detection, zone, audit, history = (
            original.detection,
            original.original_zone,
            original.selection_audit_json,
            original.family_history_json,
        )
    common = {
        "intent": intent,
        "policy": policy.prefix,
        "source_sha256": evidence["source_sha256"],
        "quote_bundle_sha256": source.packet.bundle_sha256,
        "bid": quote.ticker.bid,
        "ask": quote.ticker.ask,
        "funding_rate": quote.funding.forecast.rate,
        "values": values,
        "gate": gate,
    }
    if family:
        try:
            history = conditions._evaluate_owned_history_g2_g4_v2(
                market,
                analysis,
                **common,
                detection=detection,
                original_source_sha256=evidence["source_sha256"]
                if original is None
                else original.source_sha256,
                original_history_json=history,
            )
        except (ValueError, TypeError, AttributeError, KeyError):
            if len(gates) == 1:
                gate(
                    QualificationGate.REGIME,
                    "live_history_unknown",
                    "Required source-derived family chronology or permission is unavailable.",
                    {"history_complete": False, "unknown_is_trend": False},
                )
            else:
                raise LiveQualificationMathError(
                    "live_family_history_replay_refused"
                ) from None
    else:
        conditions._evaluate_base_g2_g4(market, analysis, **common)
    if len(gates) != 4 or not all(g.passed for g in gates):
        return finish()
    if detection is None:
        detection = extract_trigger(
            market,
            analysis,
            report_id=intent.report_id,
            strategy=intent.strategy,
            direction=intent.direction,
            observed_at=now,
            trigger_ttl_seconds=timing_policy.trigger_ttl_seconds,
        )
    values["trigger"] = detection.trigger
    code = (
        detection.fail_codes[0]
        if detection.fail_codes
        else "trigger_missing"
        if detection.trigger is None
        else "trigger_invalidated"
        if detection.trigger.invalidation_reason
        else "trigger_expired"
        if now >= detection.trigger.expires_at
        else "passed"
    )
    if original is not None:
        old = decode(original.source_json.encode(), data.MAX_SOURCE_BYTES)
        old_market = MarketSnapshot.model_validate_json(
            canonical(old["market"]), strict=True
        )
        old_analysis = MultiTimeframeAnalysis.model_validate_json(
            canonical(old["analysis"]), strict=True
        )
        joined = join_confirmed_original_path_v1(
            old_market, market, detection=detection
        )
        path = evaluate_fixed_original_event_path_v1(
            old_market, old_analysis, joined, detection=detection, observed_at=now
        )
        path_hash = path.evaluation_sha256
        if not path.required_event_path_complete:
            code = "original_event_path_incomplete"
    if not gate(
        QualificationGate.TRIGGER,
        code,
        "The fixed original closed-candle event must remain valid; no replacement trigger is extracted.",
        {
            "event_key": event_identity(detection),
            "original_event_unchanged": True,
            "event_path_sha256": path_hash,
        },
    ):
        return finish()
    executable = quote.ticker.ask if intent.direction == "long" else quote.ticker.bid
    values["reference_price"] = executable
    if consumed_event_keys is None:
        values["entry_timing_state"] = "wait"
        gate(
            QualificationGate.TIMING,
            "event_ledger_unknown",
            "WAIT for the complete account-scoped consumed-event ledger; unknown is not an empty set.",
            {"event_ledger_complete": False, "action": "WAIT"},
        )
        return finish()
    effective_expiry = min(intent.expires_at, source.expires_at)
    if original is not None:
        effective_expiry = min(effective_expiry, original.original_zone.expires_at)
    timing = evaluate_timing(
        detection,
        current_time=now,
        candidate_created_at=intent.created_at,
        candidate_expires_at=effective_expiry,
        reference_price=executable,
        consumed_event_keys=consumed_event_keys,
        policy=timing_policy,
    )
    values["entry_timing_state"] = (
        "valid"
        if timing.timing_valid
        else "wait"
        if timing.action == "WAIT"
        else "cancel"
    )
    if not gate(
        QualificationGate.TIMING,
        timing.code,
        timing.reason,
        {
            "event_key": timing.event_key,
            "action": timing.action,
            "event_ledger_authenticated": False,
        },
    ):
        return finish()
    if zone is None:
        builder = (
            location.build_original_range_anchor_zone
            if intent.strategy == "range_reversal"
            else location.build_entry_zone
        )
        zone, code = builder(
            detection,
            tick_size=policy.prefix.tick_size,
            max_allowed_drift_bps=policy.prefix.max_allowed_drift_bps,
            expires_at=timing.latest_valid_entry_time,
        )
    else:
        code = "passed"
    values["entry_zone"] = zone
    audit_values = {}
    if zone is None:
        code = "source_entry_zone_unavailable"
    elif not zone.created_at <= now < zone.expires_at:
        code = "entry_zone_expired"
    else:
        with localcontext(Context(prec=100)):
            code, _ = location._evaluate_location_numeric_tail(
                entry=intent.candidate_entry,
                direction=intent.direction,
                zone=zone,
                reference=executable,
                bid=quote.ticker.bid,
                ask=quote.ticker.ask,
                mark_price=quote.mark.price,
                audit=audit_values,
            )
    if not gate(
        QualificationGate.LOCATION,
        code,
        "Original entry and fixed source zone must still contain the executable quote.",
        {"entry_unchanged": True, "drift_bps": audit_values.get("drift_bps")},
    ):
        return finish()
    if original is None:
        selected = _selection(
            detection, market, analysis, policy, intent.candidate_entry, now
        )
        audit = selected.to_audit_json()
    else:
        # Replay selection only on its original source/time; current source is
        # supplied solely to the fixed-bracket checker, never to a new selector.
        selected = _selection(
            detection,
            old_market,
            old_analysis,
            policy,
            intent.candidate_entry,
            original.source.evaluated_at,
        )
        _need(selected.to_audit_json() == audit, "live_original_bracket_changed")
    bracket = selected.selected if selected.protection_valid else None
    code = (
        "passed"
        if bracket is not None
        else selected.fail_codes[0]
        if selected.fail_codes
        else "no_valid_structural_bracket"
    )
    if bracket is not None:
        values["stop_loss"] = bracket.stop.final_stop
    if original is not None and bracket is not None:
        _need(
            (bracket.stop.final_stop, bracket.target.final_target)
            == (original.result.stop_loss, original.result.take_profit),
            "live_original_prices_changed",
        )
        checked = fixed_protection._evaluate_owned_fixed_protection_profile(
            old_market,
            old_analysis,
            market,
            analysis,
            detection=detection,
            original_selection_audit_json=audit,
            stop_loss=original.result.stop_loss,
            take_profit=original.result.take_profit,
            entry=intent.candidate_entry,
            policy=policy.protection,
            tick_size=policy.prefix.tick_size,
            data_policy=policy.prefix.data,
            quote=quote,
            observed_at=now,
            _quote_profile=fixed_protection._OwnedQuoteProfile.LIVE,
            _current_analysis=tail.current_analysis,
        )
        fixed_hash = checked.evaluation_sha256
        if not checked.passed:
            code = "fixed_original_protection_rejected"
    if not gate(
        QualificationGate.STOP,
        code,
        "A complete source-selected original bracket is required.",
        {
            "selection_sha256": sha(audit.encode()),
            "fixed_protection_sha256": fixed_hash,
        },
    ):
        return finish()
    values["take_profit"] = bracket.target.final_target
    if not gate(
        QualificationGate.TARGET,
        "passed"
        if bracket.valid and not bracket.target.rejection_codes
        else "structural_target_invalid",
        "The original target remains fixed in the same structural bracket.",
        {"target_unchanged": True},
    ):
        return finish()
    if policy.economics is None:
        gate(
            QualificationGate.ECONOMICS,
            "economics_policy_missing",
            "Explicit original costs are required.",
            {"costs_known": False},
        )
        return finish()
    if any(
        not timedelta(0)
        <= now - at
        <= timedelta(seconds=policy.economics.maximum_quote_age_seconds)
        for at in (
            quote.ticker.source_generated_at,
            quote.mark.exchange_data_return_at,
            quote.funding.exchange_data_return_at,
            quote.capture_completed_at,
        )
    ):
        gate(
            QualificationGate.ECONOMICS,
            "economics_quote_stale",
            "Original cost freshness remains binding.",
            {"quote_fresh": False},
        )
        return finish()
    scenarios = tuple(
        costs._scenario(
            entry=entry,
            stop=values["stop_loss"],
            target=values["take_profit"],
            direction=intent.direction,
            quote=quote,
            policy=policy.economics,
        )
        for entry in (intent.candidate_entry, executable)
    )
    candidate, current = scenarios
    if candidate.math_checks_passed:
        with localcontext(Context(prec=100)):
            values["gross_rr"] = candidate.measured("gross_rr").quantize(
                Decimal("1e-30")
            )
            values["net_rr"] = candidate.measured("net_rr").quantize(Decimal("1e-30"))
    code = candidate.code if not candidate.math_checks_passed else current.code
    if not gate(
        QualificationGate.ECONOMICS,
        code,
        "Both original candidate and executable-reference economics must pass without repricing.",
        {
            "candidate_code": candidate.code,
            "execution_code": current.code,
            "candidate_net_rr": candidate.measured("net_rr"),
            "execution_net_rr": current.measured("net_rr"),
        },
    ):
        return finish()
    comparison = dict(
        costs._comparison(
            candidate,
            current,
            intent.candidate_entry,
            executable,
            intent.direction,
            values["stop_loss"],
        )
    )
    worst_cost = max(
        candidate.measured("cost_per_base"), current.measured("cost_per_base")
    )
    risk_operands = canonical(
        {
            "schema_version": "ctcc.live_qualification_risk_operands.v1",
            "report_id": intent.report_id,
            "instrument_id": intent.instrument_id,
            "direction": intent.direction,
            "candidate_entry": str(intent.candidate_entry),
            "executable_reference": str(executable),
            "stop_loss": str(values["stop_loss"]),
            "take_profit": str(values["take_profit"]),
            "round_trip_cost_per_base": str(worst_cost),
            "worst_cost_adjusted_risk_per_base": str(
                comparison["worst_cost_adjusted_risk_per_base"]
            ),
            "event_key": event_identity(detection),
            "expires_at": min(
                intent.expires_at, zone.expires_at, source.expires_at
            ).isoformat(),
            "policy_sha256": engine._hash(policy),
            "source_sha256": evidence["source_sha256"],
            "execution_authority": False,
        }
    )
    if risk_inputs is None:
        gate(
            QualificationGate.RISK,
            "live_financial_snapshot_required",
            "WAIT for complete typed Live financial and account operands; unknown history is not zero.",
            {"account_complete": False, "risk_operands_sha256": sha(risk_operands)},
        )
        return finish()
    sampled_risk = tuple(
        _sample_risk(
            risk_inputs,
            intent=intent,
            policy=policy,
            entry=entry,
            stop=values["stop_loss"],
            cost=worst_cost,
            now=now,
        )
        for entry in (intent.candidate_entry, executable)
    )
    failures = tuple(item for item in sampled_risk if not item.passed)
    if failures:
        risk = failures[0]
    else:
        _need(all(type(item.max_loss_amount) is Decimal for item in sampled_risk))
        risk = max(sampled_risk, key=lambda item: item.max_loss_amount)
    gate(
        QualificationGate.RISK,
        risk.code,
        risk.reason,
        {
            "candidate_risk_evidence_sha256": sampled_risk[0].evidence_sha256,
            "execution_risk_evidence_sha256": sampled_risk[1].evidence_sha256,
            "max_loss_amount": risk.max_loss_amount,
            "risk_pct": risk.risk_pct,
            "account_evidence_authenticated": False,
            "atomic_risk_reserved": False,
        },
    )
    return finish()
