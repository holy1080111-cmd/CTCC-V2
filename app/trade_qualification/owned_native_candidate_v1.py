"""MOVE genuine local G1–G10 and a newly issued finite account into G11.

V2 public/quote observations retain their types and funding-time meanings.
This is not a legacy PreEvidenceRun adapter. Serializable facts are audit data;
only the live registry entry can continue to this invocation's G12.
"""

import asyncio
import json
import sys as _native_sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from weakref import WeakKeyDictionary, finalize

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import captured_execution_mode_v1 as _execution_mode
from app.trade_qualification import control_bound_ledger as _native_control
from app.trade_qualification import events as _native_events
from app.trade_qualification import fixed_original_event_path_v1 as _native_path
from app.trade_qualification import native_account_risk_assembly_v1 as account
from app.trade_qualification import native_continuation_guard_v1 as _native_guard
from app.trade_qualification import native_dynamic_structural_risk_v1 as _dynamic_risk
from app.trade_qualification import (
    native_original_local_current55_v2 as _native_local55,
)
from app.trade_qualification import owned_current_original_prefix_v1 as local
from app.trade_qualification import portfolio as _native_portfolio
from app.trade_qualification.models import (
    EntryQualificationResult,
    GateAssessment,
    QualificationGate,
)

_G11_KEY_V1 = object()
_DYNAMIC_RISK_KEY_V1 = object()
_G12_KEY_V1 = object()
_OWNERS = WeakKeyDictionary()


class NativeCandidateError(ValueError):
    """Fixed source-safe codes; retained source/SQL/private values never escape."""


def _deny(code="native_original_candidate_unavailable"):
    raise NativeCandidateError(code) from None


class OwnedNativeOriginalCandidateV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny()

    def __copy__(self):
        _deny()

    def __deepcopy__(self, memo):
        _deny()

    def __reduce_ex__(self, protocol):
        _deny()

    def close(self):
        record = _OWNERS.pop(self, None)
        if record is not None:
            _release(record)


@dataclass(frozen=True, slots=True)
class NativeOriginalCandidateFactsV1:
    result: EntryQualificationResult
    candidate_json: bytes
    candidate_sha256: str
    original_policy_sha256: str
    original_event_key: str
    original_source_sha256: str
    created_at: datetime
    expires_at: datetime
    requested_contracts: Decimal
    requested_leverage: int
    execution_authority: bool = field(default=False, init=False)


def _take_local(owner):
    if type(owner) is _native_local55.OwnedNativeOriginalLocalV2:
        return "native_current55_v2", _native_local55._take_for_g11_v2(
            owner, key=_G11_KEY_V1
        )
    if type(owner) is local.OwnedCurrentOriginalLocalGatesV13:
        return "original_local_v13", local._take_original_local_for_g11_v1(
            owner, key=_G11_KEY_V1
        )
    _deny("native_original_exact_local_owner_required")


def _local_replay(record):
    if record["local_kind"] == "native_current55_v2":
        return _native_local55._replay_for_g11_v2(
            record["local_binding"], key=_G11_KEY_V1
        )
    if record["local_kind"] == "original_local_v13":
        return local._replay_original_local_for_g11_v1(
            record["local_binding"], key=_G11_KEY_V1
        )
    _deny("native_original_exact_local_contract_required")


def _release_local(kind, binding):
    if kind == "native_current55_v2":
        _native_local55._release_for_g11_v2(binding, key=_G11_KEY_V1)
    elif kind == "original_local_v13":
        local._release_original_local_for_g11_v1(binding, key=_G11_KEY_V1)
    else:
        _deny("native_original_exact_local_contract_required")


def _release(record):
    # No exchange IO, deletion, DB rollback or retry. Accepted issuance rows and
    # any publication survive all later failures. Every registry hold is spent.
    if record.get("closed", False):
        return
    record["closed"] = True
    try:
        _release_local(record["local_kind"], record["local_binding"])
    finally:
        account._release_issued_account_for_g11_v1(
            record["account_binding"], key=_G11_KEY_V1
        )


def _replay(record):
    _native_guard.check_saved_native_guard_v1(record)
    if record.get("closed", False):
        _deny()
    local_record, replay = _local_replay(record)
    issued = account._replay_issued_account_for_g11_v1(
        record["account_binding"], key=_G11_KEY_V1
    )
    if record["local_kind"] == "native_current55_v2":
        account.require_issued_original_frame_context_v1(
            record["account_binding"], record["local_binding"], key=_G11_KEY_V1
        )
    frame = local_record["prefix"]["frame"]
    now = native_stamp()
    at = utc_from_ns(now["utc_ns"])
    captured_control = decode(local_record["control"])
    original_control = _native_control.control.decode(
        _native_control.control.canonical(captured_control["state"])
    )
    _native_control.guard_current_control(
        original_control,
        captured_control["event_sha256"],
        issued.control_expectation,
        issued.scope,
        now=at,
    )
    if (
        issued.scope != local_record["scope"]
        or issued.session_factory is not frame["factory"]
        or issued.read_session is not frame["read_session"]
        or issued.private_client is not frame["private_client"]
        or issued.plan.session_binding_id != frame["plans"][1].session_binding_id
        or issued.plan.expected_uid != frame["plans"][1].expected_uid
        or issued.plan.settlement_currency != frame["plans"][1].settlement_currency
        or issued.control_expectation.scope.account_id != issued.scope.account_id
        or at >= record["deadline"]
        or now["monotonic_ns"] >= issued.expiry_monotonic_ns
    ):
        _deny("native_original_candidate_identity_or_expiry_changed")
    if record.get("facts") is not None:
        facts = record["facts"]
        if (
            type(facts) is not NativeOriginalCandidateFactsV1
            or type(facts.candidate_json) is not bytes
            or facts.candidate_json != record["candidate_raw"]
            or facts.candidate_sha256 != record["candidate_sha256"]
            or sha(facts.candidate_json) != facts.candidate_sha256
        ):
            _deny("native_original_candidate_lineage_changed")
        original_document = decode(facts.candidate_json)
        _, dynamic = _original_dynamic_risk(
            local_record,
            issued,
            at=datetime.fromisoformat(original_document["g11_evaluated_at"]),
            contracts=facts.requested_contracts,
            leverage_ceiling=original_document["caller_leverage_ceiling"],
            deadline=record["deadline"],
        )
        if (
            dynamic.selected_leverage != facts.requested_leverage
            or dynamic.receipt_json != canonical(original_document["dynamic_risk"])
            or dynamic.receipt_sha256 != original_document["dynamic_risk_sha256"]
        ):
            _deny("native_original_dynamic_risk_replay_changed")
        selected_execution = _execution_mode.select_captured_isolated_execution_v1(
            issued.execution_mode,
            instrument_id=local_record["computed"].intent.instrument_id,
            direction=facts.result.direction,
            requested_leverage=facts.requested_leverage,
        )
        if (
            issued.issued_row_sha256 != record["issued_row_sha256"]
            or local_record["result_sha256"] != record["local_result_sha256"]
            or issued.control_expectation != record["control_expectation"]
            or original_document["result"]
            != facts.result.model_dump(mode="json", round_trip=True)
            or original_document["requested_contracts"]
            != str(facts.requested_contracts)
            or original_document["requested_leverage"] != facts.requested_leverage
            or original_document["original_event_key"] != facts.original_event_key
            or facts.original_event_key
            != local.event_identity(local_record["computed"].detection)
            or original_document["original_policy_sha256"]
            != facts.original_policy_sha256
            or facts.original_policy_sha256
            != issued.control_expectation.pins.policy_sha256
            or facts.original_source_sha256 != local_record["computed"].g1.source_sha256
            or facts.created_at != local_record["created_at"]
            or original_document["created_at"] != facts.created_at.isoformat()
            or facts.expires_at != record["deadline"]
            or original_document["expires_at"] != facts.expires_at.isoformat()
            or facts.execution_authority is not False
            or canonical(original_document["execution_mode"])
            != selected_execution.receipt_json
        ):
            _deny("native_original_candidate_lineage_changed")
    return local_record, replay, issued, now


def _original_dynamic_risk(
    original, issued, *, at, contracts, leverage_ceiling, deadline
):
    policy = decode(original["policy_raw"])
    economics_policy = local.EconomicsPolicy.model_validate_json(
        json.dumps(policy["economics"]), strict=True
    )
    context = local._replay_local_quote_v13(
        original["public_packet"],
        original["quote_value"],
        original["computed"],
        evaluated_at=at,
    )
    result = original["result"]
    costs = local._economics_local_quote_v13(
        original,
        context,
        stop=result.stop_loss,
        target=result.take_profit,
        policy=economics_policy,
        observed_at=at,
    )
    if not costs.passed:
        _deny("native_original_candidate_costs_rejected")
    dynamic = _dynamic_risk.evaluate_actor_native_dynamic_risk_v1(
        original,
        issued,
        result=result,
        analysis=original["analysis"],
        entry=result.candidate_entry,
        cost_per_base=costs.cost_per_base,
        raw_score=result.raw_score,
        effective_score=result.effective_score,
        contracts=contracts,
        leverage_ceiling=leverage_ceiling,
        expires_at=deadline,
        key=_DYNAMIC_RISK_KEY_V1,
    )
    return costs, dynamic


def combine_owned_original_g11_v1(
    original_local_owner,
    issued_account_owner,
    *,
    requested_contracts,
    requested_leverage,
):
    """Fix original size once; G11 gets all account/cap facts from its issuer.

    Leverage input is a ceiling; only actual captured configuration below the
    computed quality cap can be selected. Size is fixed before G11 and cannot
    be changed by post-publication continuation.
    """
    if (
        type(requested_contracts) is not Decimal
        or not requested_contracts.is_finite()
        or not Decimal(0) < requested_contracts < Decimal("1e20")
        or len(requested_contracts.as_tuple().digits) > 40
        or requested_contracts.as_tuple().exponent < -20
        or type(requested_leverage) is not int
        or not 1 <= requested_leverage <= 20
    ):
        _deny("native_original_candidate_size_invalid")
    _native_path._require_event_policy_v1()
    local_kind, local_binding = _take_local(original_local_owner)
    try:
        account_binding = account._take_issued_account_for_g11_v1(
            issued_account_owner, key=_G11_KEY_V1
        )
    except BaseException:
        _release_local(local_kind, local_binding)
        raise
    record = {
        "local_kind": local_kind,
        "local_binding": local_binding,
        "account_binding": account_binding,
    }
    _native_guard.save_native_guard_v1(record, _NATIVE_GUARD)
    retained = False
    try:
        if local_kind == "native_current55_v2":
            _native_local55._adopt_g11_binding_v2(
                local_binding, account_binding, key=_G11_KEY_V1
            )
        initial, _ = _local_replay(record)
        issued = account._replay_issued_account_for_g11_v1(
            account_binding, key=_G11_KEY_V1
        )
        intent = initial["computed"].intent
        record["deadline"] = min(
            initial["prefix"]["frame"]["deadline"],
            intent.expires_at,
            initial["result"].entry_zone.expires_at,
            initial["computed"].detection.trigger.expires_at,
            issued.expires_at,
        )
        original, _, issued, stamp = _replay(record)
        at = utc_from_ns(stamp["utc_ns"])
        result = original["result"]
        leverage_ceiling = requested_leverage
        costs, dynamic = _original_dynamic_risk(
            original,
            issued,
            at=at,
            contracts=requested_contracts,
            leverage_ceiling=leverage_ceiling,
            deadline=record["deadline"],
        )
        requested_leverage = dynamic.selected_leverage
        selected_execution = _execution_mode.select_captured_isolated_execution_v1(
            issued.execution_mode,
            instrument_id=intent.instrument_id,
            direction=result.direction,
            requested_leverage=requested_leverage,
        )
        g11 = account.evaluate_issued_g11_v1(
            account_binding,
            report_id=result.report_id,
            instrument_id=intent.instrument_id,
            direction=result.direction,
            entry=result.candidate_entry,
            stop=result.stop_loss,
            cost=costs.cost_per_base,
            contracts=requested_contracts,
            leverage=requested_leverage,
            evaluated_at=at,
            key=_G11_KEY_V1,
        )
        if not g11.passed:
            _deny("native_original_g11_rejected")
        original_public = original["prefix"]["frame"]["market_state"]
        directory, _, _, events = local.frame.market.replay_runtime_bundle_v4(
            original_public["bundle_raw"],
            expected_sha256=original_public["bundle_sha256"],
        )
        account.scan_issued_original_public_v1(
            account_binding, directory, events, key=_G11_KEY_V1
        )
        values = {name: getattr(result, name) for name in type(result).model_fields}
        values["gates"] = (
            *result.gates,
            GateAssessment(
                report_id=result.report_id,
                gate=QualificationGate.RISK,
                passed=True,
                code="passed",
                reason="The same invocation's issued finite account passes fixed candidate risk.",
                measured_values={
                    "evaluation_sha256": g11.evidence_sha256,
                    "dynamic_risk_sha256": dynamic.receipt_sha256,
                    "issued_row_sha256": issued.issued_row_sha256,
                },
            ),
        )
        result = local._owned_result_from_values_v2(original["result"], values)
        raw = canonical(
            {
                "schema_version": "ctcc.native_original_candidate.v2",
                "scope": issued.scope.model_dump(mode="json", round_trip=True),
                "original_event_key": local.event_identity(
                    original["computed"].detection
                ),
                "event_path_policy_sha256": _native_path.POLICY_SHA256,
                "result": result.model_dump(mode="json", round_trip=True),
                "intent": intent.model_dump(mode="json", round_trip=True),
                "detection": original["computed"].detection.model_dump(mode="json"),
                "public_packet_sha256": original["public_packet"].bundle_sha256,
                "local_policy_sha256": sha(original["policy_raw"]),
                "full_risk_policy_sha256": issued.policy_sha256,
                "original_policy_sha256": issued.control_expectation.pins.policy_sha256,
                "issued_row_sha256": issued.issued_row_sha256,
                "issuer_sha256": issued.issuer_sha256,
                "credential_session_sha256": issued.control_expectation.pins.credential_session_sha256,
                "account_revision": issued.account_revision,
                "ledger_revision": issued.ledger_revision,
                "requested_contracts": str(requested_contracts),
                "requested_leverage": requested_leverage,
                "caller_leverage_ceiling": leverage_ceiling,
                "dynamic_risk": decode(dynamic.receipt_json),
                "dynamic_risk_sha256": dynamic.receipt_sha256,
                "execution_mode": decode(selected_execution.receipt_json),
                "created_at": original["created_at"].isoformat(),
                "g11_evaluated_at": at.isoformat(),
                "expires_at": record["deadline"].isoformat(),
                "g11": g11.model_dump(mode="json", round_trip=True),
            }
        )
        account.scan_issued_metadata_v1(account_binding, raw, key=_G11_KEY_V1)
        facts = NativeOriginalCandidateFactsV1(
            result,
            raw,
            sha(raw),
            issued.control_expectation.pins.policy_sha256,
            local.event_identity(original["computed"].detection),
            original["computed"].g1.source_sha256,
            original["created_at"],
            record["deadline"],
            requested_contracts,
            requested_leverage,
        )
        record.update(
            facts=facts,
            issued_row_sha256=issued.issued_row_sha256,
            local_result_sha256=original["result_sha256"],
            control_expectation=issued.control_expectation,
            candidate_raw=raw,
            candidate_sha256=sha(raw),
        )
        _replay(record)
        owner = object.__new__(OwnedNativeOriginalCandidateV1)
        _OWNERS[owner] = record
        record["finalizer"] = finalize(owner, _release, record)
        retained = True
        return owner
    except asyncio.CancelledError:
        raise
    except NativeCandidateError:
        raise
    except Exception:  # noqa: BLE001 -- preserve actual ownership and fail closed on unknown/cancellation.
        _deny("native_original_candidate_assembly_failed")
    finally:
        if not retained:
            _release(record)


def _take_native_candidate_for_g12_v1(owner, *, key):
    from app.trade_qualification import native_g12_publication_v2 as publication

    if key is not publication._CANDIDATE_KEY_V2:
        _deny("native_original_g12_fixed_purpose_required")
    record = (
        _OWNERS.pop(owner, None)
        if type(owner) is OwnedNativeOriginalCandidateV1
        else None
    )
    if record is None:
        _deny()
    record["finalizer"].detach()
    try:
        _replay(record)
        return record
    except BaseException:
        _release(record)
        raise


_NATIVE_GUARD = _native_guard.install_native_guards_v1(
    _native_sys.modules[__name__],
    ("combine_owned_original_g11_v1",),
    dependencies=(
        local,
        account,
        _native_portfolio,
        _native_path,
        _native_events,
        _native_path.continuation,
        local._local_protection_v13,
        local._local_bridge_v2,
        local._local_current_economics_v2,
        local._local_location_v13,
        local._local_economics_v13,
        local._local_timing_v13,
        local.public_v2,
        _execution_mode,
        _dynamic_risk,
        _dynamic_risk.risk_profile,
        _dynamic_risk.structural_risk,
        _dynamic_risk.mathematics,
        _dynamic_risk.derivatives,
        _dynamic_risk.risk_config,
        _dynamic_risk.financial,
        _dynamic_risk.financial.configured,
        _dynamic_risk.financial.portfolio,
        _dynamic_risk.hard,
        _dynamic_risk.claims_codec,
        _dynamic_risk.protection,
        _dynamic_risk.candidate_models,
        _native_local55,
        _native_control,
    ),
)
